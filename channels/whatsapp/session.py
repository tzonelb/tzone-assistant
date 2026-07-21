class WhatsAppOptionSession:
    def __init__(self):
        self.options = {}

    def save_options(self, user_id, buttons):
        self.options[str(user_id)] = {
            str(index + 1): button
            for index, button in enumerate(buttons or [])
        }

    def resolve_message(self, user_id, message):
        user_options = self.options.get(str(user_id), {})

        if message in user_options:
            return user_options[message]

        return message


whatsapp_options = WhatsAppOptionSession()