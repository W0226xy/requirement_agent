from pydantic import BaseModel, ConfigDict


class StrictAIModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

