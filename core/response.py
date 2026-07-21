class Response:

    def __init__(self, text, buttons=None):
        self.text = text
        self.buttons = buttons or []

    def to_dict(self):
        return {
            "text": self.text,
            "buttons": self.buttons
        }