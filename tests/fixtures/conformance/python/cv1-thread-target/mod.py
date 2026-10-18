import threading


class Worker:
    def run(self):
        pass

    def start(self):
        threading.Thread(target=self.run).start()
