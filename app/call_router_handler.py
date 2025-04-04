from app.unified_logger import get_logger

logger = get_logger()

# The idea behind this table approach is to map a fixed set of ACS phone numbers to their corresponding agent IDs, system blurbs, and knowledge base configurations.
# This allows for easy routing of calls and retrieval of relevant information based on the incoming phone number.

# Lookup table for agent routing
lookup_table = {
    "+1234567890": "+1234567890"
}

# blurb table is for setting up the system blurb for the agent
blurb_table = {
    "+1234567890": "Dummy Service"
}

# knowledge base table is for setting up the search configuration 
knowledge_base_table = {
    "+1234567890": {
        "SEARCH_INDEX": "dummy-index",
        "SEARCH_SEMANTIC_CONFIGURATION": "dummy-semantic-config"
    }
}

def agent_router_handler(acs_phone_number: str):
    """
    Route the call to the appropriate agent based on the agent_id.
    """
    if acs_phone_number not in lookup_table:
        logger.error(f"Invalid phone number: {acs_phone_number}")
        return None
    return lookup_table[acs_phone_number]

def system_blurb_handler(acs_phone_number: str):
    """
    Route the call to the appropriate system blurb based on the agent_id.
    """
    if acs_phone_number not in blurb_table:
        logger.error(f"Invalid phone number: {acs_phone_number}")
        return None
    return blurb_table[acs_phone_number]

def knowledge_base_handler(acs_phone_number: str):
    """
    Fetch Dynamic search configuration based on incoming mobile number.
    """
    if acs_phone_number not in knowledge_base_table:
        logger.error(f"Invalid phone number: {acs_phone_number}")
        return None
    return knowledge_base_table[acs_phone_number]