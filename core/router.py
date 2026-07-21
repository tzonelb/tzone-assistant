class Router:

    def __init__(self):
        self.routes = {}

    def register(self, key, handler):
        self.routes[key] = handler

    def dispatch(self, key):
        handler = self.routes.get(key)

        if handler:
            return handler()

        return "Route not found."


router = Router()