"""Strict New API-only media model factory."""

from .newapi import NewAPIImageModel, NewAPIVideoModel
from ..utils.newapi_models import IMAGE, VIDEO, get_model_spec

class ModelFactory:
    @staticmethod
    def create_model(config):
        model_config = config.get("model") or {}
        model_name = config.get("model.name") or model_config.get("name")
        spec = get_model_spec(model_name)
        if spec.capability == IMAGE:
            return NewAPIImageModel(model_config)
        if spec.capability == VIDEO:
            return NewAPIVideoModel(model_config)
        raise ValueError(f"Model '{model_name}' is not a media generation model")
