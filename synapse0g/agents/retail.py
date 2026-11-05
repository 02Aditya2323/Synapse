"""tau-bench retail: a customer-service agent with real money-moving tools, judged by database state.

Source: https://github.com/sierra-research/tau-bench (MIT), pinned below. We use its data, tools,
tasks, policy text and reward unchanged. Two pieces are rewritten here because the originals call
LiteLLM, which we do not install:

  - the agent loop is tau-bench's reference `ToolCallingAgent.solve`, step for step, with the
    OpenAI SDK pointed at the synapse0g endpoint in place of `litellm.completion`;
  - the simulated customer is tau-bench's `LLMUserSimulationEnv` prompt, on a fixed model in a
    separate session. Its calls are never optimized and never counted as the agent's cost.

A task passes when the final database equals the one the reference actions produce and every
required fact appears in the agent's replies: tau-bench's own reward.

The agent changes orders, refunds and exchanges. Here it does so in a fresh in-memory copy of
tau-bench's database for every task, which is the only place such actions may run during
optimization. In production, those tools sit behind the approval gate.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from hashlib import sha256

from synapse0g import effects
from synapse0g.agents.langgraph_agents import AgentResult
from synapse0g.registry import ROOT
from synapse0g.tasks import Task

TAU_DIR = ROOT / "data" / "vendor" / "tau-bench"
TAU_COMMIT = "59a200c"
RESPOND = "respond"
MAX_STEPS = 30

# tools that change nothing: labelled by us, as a developer would
LABELS = {"think": "pure", "calculate": "pure", "transfer_to_human_agents": "external"}


def ensure_tau() -> None:
    if not (TAU_DIR / "tau_bench").exists():
        TAU_DIR.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "-q", "https://github.com/sierra-research/tau-bench.git", str(TAU_DIR)], check=True)
        subprocess.run(["git", "-C", str(TAU_DIR), "checkout", "-q", TAU_COMMIT], check=True)
    if "litellm" not in sys.modules:
        sys.modules["litellm"] = _litellm_stub()
    if str(TAU_DIR) not in sys.path:
        sys.path.insert(0, str(TAU_DIR))


def _litellm_stub():
    """tau-bench imports litellm at load time; we never let it call a model. Every model call
    goes through the synapse0g endpoint, so a call through this stub is a bug and fails loudly."""
    import types

    def completion(*args, **kwargs):
        raise RuntimeError("tau-bench tried to call a model through litellm; synapse0g routes every call through its proxy")

    stub = types.ModuleType("litellm")
    stub.completion = completion
    return stub


def load_retail(split: str) -> list[Task]:
    """tau-bench's own split: `train` tasks for tuning, `test` tasks for reporting."""
    return [Task(f"tau-retail-{split}-{i:03d}", "retail", t.instruction, "", (split, i)) for i, t in enumerate(_tau_tasks(split))]


# ---- the environment: tau_bench.envs.base.Env without the LiteLLM user ------------------
def _hashable(item):
    if isinstance(item, dict):
        return tuple((k, _hashable(v)) for k, v in sorted(item.items()))
    if isinstance(item, list):
        return tuple(_hashable(e) for e in item)
    if isinstance(item, set):
        return tuple(sorted(_hashable(e) for e in item))
    return item


def _data_hash(data) -> str:
    return sha256(str(_hashable(data)).encode()).hexdigest()


class RetailEnv:
    def __init__(self, task: Task):
        ensure_tau()
        from tau_bench.envs.retail.data import load_data
        from tau_bench.envs.retail.tools import ALL_TOOLS
        from tau_bench.envs.retail.wiki import WIKI

        split, index = task.context
        self.tau_task = _tau_tasks(split)[index]
        self.load_data = load_data
        self.data = load_data()
        self.tools_map = {t.get_info()["function"]["name"]: t for t in ALL_TOOLS}
        self.tools_info = [t.get_info() for t in ALL_TOOLS]
        self.wiki = WIKI
        self.terminate_tools = ["transfer_to_human_agents"]
        self.actions: list[tuple[str, dict]] = []

    def run_tool(self, name: str, kwargs: dict) -> str:
        self.actions.append((name, kwargs))
        if name not in self.tools_map:
            return f"Unknown action {name}"
        try:
            return self.tools_map[name].invoke(data=self.data, **kwargs)
        except Exception as e:  # tau-bench returns tool errors to the agent as text
            return f"Error: {e}"

    def reward(self, replies: list[str]) -> tuple[float, dict]:
        """tau_bench.envs.base.Env.calculate_reward, unchanged in substance."""
        got = _data_hash(self.data)
        expected_data = self.load_data()
        for action in self.tau_task.actions:
            if action.name not in self.terminate_tools and action.name in self.tools_map:
                try:
                    self.tools_map[action.name].invoke(data=expected_data, **action.kwargs)
                except Exception:  # the reference actions are replayed exactly as tau-bench does
                    pass
        info = {"database_matches": got == _data_hash(expected_data)}
        reward = 1.0 if info["database_matches"] else 0.0
        if self.tau_task.outputs:
            found = {o: any(o.lower() in r.lower().replace(",", "") for r in replies) for o in self.tau_task.outputs}
            info["outputs"] = found
            if not all(found.values()):
                reward = 0.0
        return reward, info


def _tau_tasks(split: str):
    ensure_tau()
    if split == "train":
        from tau_bench.envs.retail.tasks_train import TASKS_TRAIN as tasks
    else:
        from tau_bench.envs.retail.tasks_test import TASKS_TEST as tasks
    return tasks


USER_PROMPT = """You are a user interacting with an agent.{instruction}
Rules:
- Just generate one line at a time to simulate the user's message.
- Do not give away all the instruction at once. Only provide the information that is necessary for the current step.
- Do not hallucinate information that is not provided in the instruction. For example, if the agent asks for the order id but it is not mentioned in the instruction, do not make up an order id, just say you do not remember or have it.
- If the instruction goal is satisified, generate '###STOP###' as a standalone message without anything else to end the conversation.
- Do not repeat the exact instruction in the conversation. Instead, use your own words to convey the same information.
- Try to make the conversation as natural as possible, and stick to the personalities in the instruction."""


def make_retail_agent(engine, user_model: str):
    """Return an agent function with the standard signature. The simulated customer runs in its own
    unoptimized session on `user_model`; its cost is reported separately."""
    from openai import OpenAI

    def run(task: Task, base_url: str, token: str, model: str) -> AgentResult:
        t0 = time.perf_counter()
        user_session = engine.open()
        agent_client = OpenAI(base_url=base_url, api_key=token, max_retries=0, timeout=300)
        user_client = OpenAI(base_url=base_url, api_key=user_session.id, max_retries=0, timeout=300)
        env = RetailEnv(task)
        tool_effect = effects.tool_effects(env.tools_info, LABELS)
        user_msgs = [
            {"role": "system", "content": USER_PROMPT.format(instruction="\n\nInstruction: " + env.tau_task.instruction + "\n")},
            {"role": "user", "content": "Hi! How can I help you today?"},
        ]

        def user_says() -> str:
            res = user_client.chat.completions.create(model=user_model, messages=user_msgs, temperature=0)
            text = res.choices[0].message.content or ""
            user_msgs.append({"role": "assistant", "content": text})
            return text

        replies: list[str] = []
        protected = 0
        calls = 0
        error = None
        try:
            messages = [{"role": "system", "content": env.wiki}, {"role": "user", "content": user_says()}]
            done = False
            for _ in range(MAX_STEPS):
                res = agent_client.chat.completions.create(model=model, messages=messages, tools=env.tools_info, temperature=0)
                calls += 1
                msg = res.choices[0].message
                next_message = {"role": "assistant", "content": msg.content}
                if msg.tool_calls:
                    tc = msg.tool_calls[0]  # tau-bench: one tool call per turn
                    next_message["tool_calls"] = [{"id": tc.id, "type": "function",
                                                   "function": {"name": tc.function.name, "arguments": tc.function.arguments}}]
                    try:
                        kwargs = json.loads(tc.function.arguments or "{}")
                    except json.JSONDecodeError:
                        kwargs = {}
                    if tool_effect.get(tc.function.name, ("irreversible", ""))[0] == "irreversible":
                        protected += 1
                    observation = env.run_tool(tc.function.name, kwargs)
                    messages += [next_message, {"role": "tool", "tool_call_id": tc.id, "name": tc.function.name, "content": observation}]
                    if tc.function.name in env.terminate_tools:
                        done = True
                else:
                    content = msg.content or ""
                    replies.append(content)
                    user_msgs.append({"role": "user", "content": content})
                    observation = user_says()
                    messages += [next_message, {"role": "user", "content": observation}]
                    done = "###STOP###" in observation
                if done:
                    break
        except Exception as e:  # a crash is recorded as a crashed task, never scored as a wrong answer
            error = f"{type(e).__name__}: {e}"[:500]
        finally:
            user_records = engine.close(user_session.id).records
        user_cost = sum(r.cost_usd or 0.0 for r in user_records)
        if error:
            return AgentResult("", error, time.perf_counter() - t0, calls,
                               {"user_sim_cost_usd": user_cost, "protected_actions": protected})
        reward, info = env.reward(replies)
        answer = f"reward={reward:.0f} " + json.dumps(info)
        return AgentResult(answer, None, time.perf_counter() - t0, calls,
                           {"user_sim_cost_usd": user_cost, "protected_actions": protected, "tool_actions": len(env.actions)})

    return run
