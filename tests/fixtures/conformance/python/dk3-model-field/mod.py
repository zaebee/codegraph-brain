from pydantic import BaseModel


class Event(BaseModel):
    brand: str
