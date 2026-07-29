"""Framework-version-neutral HTTP status values used by the control plane."""

# RFC 9110 renamed status 422 to "Unprocessable Content". Supported Starlette
# versions expose different constant names, but the wire value is unchanged.
UNPROCESSABLE_CONTENT = 422
