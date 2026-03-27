
import random

class RLAgent:
    def __init__(self):
        self.q = {}

    def _key(self, state):
        return tuple(round(x, 3) for x in state)

    def choose(self, state):
        key = self._key(state)

        if key not in self.q:
            self.q[key] = {"skip":0,"small":0,"medium":0,"large":0}

        if random.random() < 0.1:
            return random.choice(list(self.q[key].keys()))

        return max(self.q[key], key=self.q[key].get)

    def update(self, state, action, reward):
        key = self._key(state)
        if key not in self.q:
            return
        self.q[key][action] += reward
