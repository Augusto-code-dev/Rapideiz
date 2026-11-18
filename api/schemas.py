from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .catalog import PRODUCTS


class Item(BaseModel):
    product_id: str
    quantity: int = Field(strict=True, ge=1, le=30)

    @field_validator("product_id")
    @classmethod
    def validate_product(cls, product_id: str) -> str:
        if product_id not in PRODUCTS:
            raise ValueError("Produto fora do catálogo")
        return product_id


class NewOrder(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    customer_name: str = Field(min_length=2, max_length=120)
    customer_phone: str = Field(min_length=8, max_length=30)
    delivery_address: str = Field(min_length=8, max_length=255)
    items: list[Item] = Field(min_length=1, max_length=len(PRODUCTS))
    payment_method: Literal["pix", "cartao", "dinheiro", "sem_limite"] = "pix"
    simulate_failure: bool = False

    @field_validator("items")
    @classmethod
    def validate_unique_products(cls, items: list[Item]) -> list[Item]:
        if len({item.product_id for item in items}) != len(items):
            raise ValueError("Informe cada produto uma única vez")
        return items
