"""A plain-Python agent for the compiler's tests: no framework, no synapse0g imports."""

import time

CALLS = []


def read_request(request):
    time.sleep(0.2)
    CALLS.append("read_request")
    return {"qty": request["qty"]}


def extract_quote(document):
    time.sleep(0.2)
    CALLS.append("extract_quote")
    return {"supplier": document["supplier"], "price": document["price"]}


def choose(requirements, quotes):
    return min(quotes, key=lambda q: q["price"])["supplier"]


def submit_purchase_request(choice):
    CALLS.append("submit")
    return {"submitted": choice}


def handle(request, documents):
    requirements = read_request(request)
    quotes = [extract_quote(d) for d in documents]
    choice = choose(requirements, quotes)
    return submit_purchase_request(choice)
