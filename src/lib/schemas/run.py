from pydantic import BaseModel


class RunContext(BaseModel):
    run_id: str
    schedule_id: str
