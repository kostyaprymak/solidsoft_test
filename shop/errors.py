from werkzeug.exceptions import Unauthorized


class AuthenticationRequired(Unauthorized):
    description = "Valid bearer token required"
