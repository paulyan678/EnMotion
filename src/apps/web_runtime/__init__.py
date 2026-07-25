"""Request-scoped runtime services for the multi-user web deployment."""

from .context import RequestTenant, bind_tenant, get_tenant, reset_tenant

__all__ = [
    "PipelineProxy",
    "RequestTenant",
    "WorkspacePipelineRegistry",
    "bind_tenant",
    "get_tenant",
    "reset_tenant",
]


def __getattr__(name: str):
    """Keep identity/context imports independent from the heavy AI pipeline."""

    if name in {"PipelineProxy", "WorkspacePipelineRegistry"}:
        from .pipeline_registry import PipelineProxy, WorkspacePipelineRegistry

        return {
            "PipelineProxy": PipelineProxy,
            "WorkspacePipelineRegistry": WorkspacePipelineRegistry,
        }[name]
    raise AttributeError(name)
