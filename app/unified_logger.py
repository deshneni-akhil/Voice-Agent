import threading
from loguru import logger
import logging
import sys

logger_lock = threading.Lock()

def initialize_logger():
    with logger_lock:
        logger.remove()  # Remove default handler
        logger.add(sys.stdout, level="DEBUG", colorize=True)  # Add stderr handler
        return logger

# Call setup_logger to configure the logger
logger = initialize_logger()

# Set the logger configuration
def set_logger(level="DEBUG"):
    global logger
    with logger_lock:
        logger.remove()  # Remove default handler
        logger.add(sys.stdout, level=level, colorize=True)
    return logger 

def get_logger():
    return set_logger(level=logging.INFO)