class SnapshotManager:
    def __init__(self):
        self.snapshots = []

    def capture_snapshot(self, metadata):
        snapshot = {'metadata': metadata}
        self.snapshots.append(snapshot)
        return snapshot

    def get_snapshots(self):
        return self.snapshots

    def clear_snapshots(self):
        self.snapshots = []
