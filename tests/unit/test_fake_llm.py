from requirement_agent.ai.llm.fake import FakeLLM


async def test_fake_embedding_supports_production_dimension() -> None:
    model = FakeLLM([], embedding_dimension=1536)

    result = await model.embed(["requirement"])

    assert len(result) == 1
    assert len(result[0]) == 1536
