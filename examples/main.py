from fastapi import FastAPI
from pydantic import BaseModel, Field

app = FastAPI()


class Article(BaseModel):
    id: int
    title: str
    description: str | None = None


@app.get("/articles/{article_id}", response_model=Article, summary="Get article")
async def get_article(article_id: int):
    """Get article"""
    return {"id": article_id, "title": "Example", "description": None}
