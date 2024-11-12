import logging

class BaseRepository:
    def __init__(self, db_session):
        self.db_session = db_session
        self.logger = logging.getLogger(__name__)

    def save(self, data):
        raise NotImplementedError("This method should be overridden by subclasses") 