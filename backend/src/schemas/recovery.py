from pydantic import BaseModel, Field
class RecoveryInput(BaseModel):
    crash_case_id: int=Field(gt=0)
    label: str=Field(min_length=2,max_length=120)
    interrupt_after: int | None=Field(default=None,ge=0,le=1000)
