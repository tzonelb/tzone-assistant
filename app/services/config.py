from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    META_VERIFY_TOKEN: str

    META_PAGE_ACCESS_TOKEN: str
    META_INSTAGRAM_ACCESS_TOKEN: str | None = None

    DATABASE_URL: str

    class Config:
        env_file = ".env"


settings = Settings()