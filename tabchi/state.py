"""Runtime (non-persistent) state."""


class Run:
    def __init__(self):
        self.task = None
        self.running = False
        self.paused = False
        self.groups = []      # cached dialogs for the group picker
        self.live = None      # panel message that is refreshed automatically
        self.pending = {}     # waiting for a text/file input from the owner
        self.reset()

    def reset(self):
        self.sent = 0
        self.failed = 0
        self.total = 0
        self.done = 0
        self.round = 0
        self.counter = 0
        self.current = ""
        self.waiting = ""
        self.started = 0.0
        self.stop_reason = "done"
        self.errors = []
        self.removed = []
        self.limit_notified = False

    @property
    def active(self):
        return self.task is not None and not self.task.done()


st = Run()
