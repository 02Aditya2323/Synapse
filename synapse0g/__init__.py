"""synapse0g: compile an agent workflow into a graph, optimize it, and prove the optimized one is safe.

    import synapse0g
    app = synapse0g.compile(builder, sample=..., effects=...)   # a LangGraph StateGraph
    client = synapse0g.wrap(OpenAI())                           # any OpenAI-compatible client
"""


def compile(builder, **kwargs):  # noqa: A001 - mirrors StateGraph.compile
    """See `synapse0g.app.compile`. Imported here lazily, so `import synapse0g` needs no LangGraph."""
    from synapse0g.app import compile as _compile

    return _compile(builder, **kwargs)


def wrap(client, **kwargs):
    """See `synapse0g.recorder.wrap`: records a client's calls and lifts them into a graph."""
    from synapse0g.recorder import wrap as _wrap

    return _wrap(client, **kwargs)
