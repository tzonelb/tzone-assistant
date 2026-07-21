import json
from pathlib import Path


class FlowLoader:
    def __init__(self):
        self.states = {}
        self.load_all_flows()

    def load_all_flows(self):
        self.states = {}
        features_path = Path("features")

        for flow_file in features_path.glob("*/flow.json"):
            with open(flow_file, "r", encoding="utf-8") as file:
                data = json.load(file)

            self.states.update(data.get("states", {}))

    def get_state(self, state_name):
        return self.states.get(state_name)


flow_loader = FlowLoader()