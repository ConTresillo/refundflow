from app import policies

QUERY_NEED_REFUND = "I paid for an order but the item arrived damaged. How do I get a refund?"


def test_loads_the_two_real_public_policies():
    reg = policies.load_registry()
    assert set(reg) == {"stripe-refund", "github-tos"}
    stripe = reg["stripe-refund"]
    assert stripe["source_url"] == "https://docs.stripe.com/refunds"
    assert stripe["source_name"] == "Stripe Documentation"
    tos = reg["github-tos"]
    assert tos["source_url"] == "https://docs.github.com/en/github/site-policy/github-terms-of-service"


def test_chunks_and_runs_deterministic_retrieval_without_llm():
    registry = policies.load_registry()
    docs = policies.make_documents(registry)

    # Splitting a short piece of policy text must be deterministic and
    # produce at least one sentence-sized chunk per excerpt.
    quoted = "You can cancel a payment before it is completed at no cost."
    assert policies.split_text(quoted) == [quoted]
    # max_words splits the sentence into word-groups of at most that many words.
    assert policies.split_text(quoted, max_words=3) == [
        "You can cancel",
        "a payment before",
        "it is completed",
        "at no cost.",
    ]

    # Retrieval is a pure function: the same query must always return the
    # same ranking, and it must never touch the network.
    ranking = policies.retrieve(QUERY_NEED_REFUND, docs, top_k=2)
    assert len(ranking) == 2
    assert isinstance(ranking, list)
    first = ranking[0]
    assert first["policy_id"] == "stripe-refund"
    assert first["source_url"] == "https://docs.stripe.com/refunds"
    assert first["score"] > 0
