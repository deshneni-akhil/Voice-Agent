import sys

from multiprocessing import connection
import os
import uuid
import time
from urllib.parse import urlencode, urlparse, urlunparse
from dotenv import load_dotenv
from typing import List, Dict, Any

# from fastapi.logger import logger
from fastapi import (
    Body,
    FastAPI,
    Form,
    WebSocket,
    HTTPException,
    Request,
    status,
    Depends,
)
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse, RedirectResponse
from azure.eventgrid import EventGridEvent, SystemEventNames
from azure.core.credentials import AzureKeyCredential
from azure.communication.callautomation import (
    MediaStreamingOptions,
    AudioFormat,
    MediaStreamingTransportType,
    MediaStreamingContentType,
    MediaStreamingAudioChannelType,
    CallAutomationClient,
    CallConnectionClient,
    PhoneNumberIdentifier,
    TextSource,
)

from app.communication_handler import CommunicationHandler
from app.cache import get_cache as get_cache_instance
from app.unified_logger import get_logger

# print(f"get_cache_instance: {get_cache_instance()}")

load_dotenv()

logger = get_logger()

app = FastAPI(
    title="ACS Call Automation Sample",
    description="Sample application to demonstrate Azure Communication Services (ACS) Call Automation.",
    version="1.0.0",
    openapi_tags=[
        {
            "name": "Call Automation",
            "description": "Endpoints for Call Automation.",
        },
        {
            "name": "WebSocket",
            "description": "WebSocket endpoints.",
        },
    ],
)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

ACS_CONNECTION_STRING = os.getenv("ACS_CONNECTION_STRING")
acs_ca_client = CallAutomationClient.from_connection_string(ACS_CONNECTION_STRING)

# Callback events URI to handle callback events.
CALLBACK_URI_HOST = os.getenv("CALLBACK_URI_HOST")
CALLBACK_EVENTS_URI = CALLBACK_URI_HOST + "/api/callbacks"


def get_callback_events_uri():
    return CALLBACK_EVENTS_URI


@app.get("/")
async def root():
    return JSONResponse(
        status_code = status.HTTP_200_OK,
        content = {
            "message": "Welcome to the ACS Call Automation Sample API. Use the endpoints to interact with the service.",
            "version": app.version,
            "description": app.description,
            "title": app.title,
            "openapi_docs": "/docs",
            "redoc_docs": "/redoc"
        },
    )



@app.post("/api/incomingCall")
async def incoming_call_handler(request: Request):
    logger.info("incoming event data")
    for event_dict in await request.json():
        event = EventGridEvent.from_dict(event_dict)
        # logger.info("incoming event data --> %s", event.data)
        if (
            event.event_type
            == SystemEventNames.EventGridSubscriptionValidationEventName
        ):
            logger.info("Validating subscription")
            validation_code = event.data["validationCode"]
            validation_response = {"validationResponse": validation_code}
            logger.info(validation_response)
            return JSONResponse(
                content=validation_response, status_code=status.HTTP_200_OK
            )
        elif event.event_type == "Microsoft.Communication.IncomingCall":
            # logger.info("Incoming call event")
            # logger.info(f"Event data: {event.data}")

            # Extracting the caller ID mobile number who is calling
            if event.data["from"]["kind"] == "phoneNumber":
                caller_id = event.data["from"]["phoneNumber"]["value"]
            else:
                caller_id = event.data["from"]["rawId"]

            # Fetching the mobile number from where the call is coming from
            acs_mobile_number = event.data["to"]["phoneNumber"]["value"]

            incoming_call_context = event.data["incomingCallContext"]
            guid = uuid.uuid4()
            # Generated guid to be used as a unique identifier for the call
            logger.info(f"GUID: {guid}")

            query_parameters = urlencode({"callerId": caller_id})
            callback_uri = f"{CALLBACK_EVENTS_URI}/{guid}?{query_parameters}"

            parsed_url = urlparse(CALLBACK_EVENTS_URI)

            # adding caller id to cache
            get_cache_instance().set(
                str(guid),
                {"caller_id": caller_id, "acs_mobile_number": acs_mobile_number},
            )

            # Use the same query parameters for both callback and websocket URLs
            query_parameters = urlencode(
                {"uuid": str(guid), "acsPhoneNumber": acs_mobile_number}
            )

            websocket_url = urlunparse(
                ("wss", parsed_url.netloc, "/ws", None, query_parameters, None)
            )

            logger.info(f"callback url: {callback_uri}")
            logger.info(f"websocket url: {websocket_url}")

            try:
                # Answer the incoming call

                media_streaming_options = MediaStreamingOptions(
                    transport_url=websocket_url,
                    transport_type=MediaStreamingTransportType.WEBSOCKET,
                    content_type=MediaStreamingContentType.AUDIO,
                    audio_channel_type=MediaStreamingAudioChannelType.MIXED,
                    start_media_streaming=True,
                    enable_bidirectional=True,
                    audio_format=AudioFormat.PCM24_K_MONO,
                )

                answer_call_result = acs_ca_client.answer_call(
                    incoming_call_context=incoming_call_context,
                    operation_context="incomingCall",
                    callback_url=callback_uri,
                    media_streaming=media_streaming_options,
                )

            except Exception as e:
                raise e

            logger.info(
                f"Answered call for connection id: {answer_call_result.call_connection_id}"
            )


@app.post("/api/callbacks/{contextId}")
async def handle_callback_with_context(contextId: str, request: Request):

    async def handle_call_connected(event_data: Dict[str, Any]):
        call_connection_id = event_data["callConnectionId"]
        call_connection_properties = acs_ca_client.get_call_connection(
            call_connection_id
        ).get_call_properties()
        media_streaming_subscription = (
            call_connection_properties.media_streaming_subscription
        )
        # adding call connection id and corelation to cache
        get_cache_instance().set(
            contextId,
            {
                "callConnectionId": call_connection_id,
                "correlationId": event_data["correlationId"],
            },
        )
        logger.info(f"MediaStreamingSubscription:--> {media_streaming_subscription}")
        logger.info(
            f"Received CallConnected event for connection id: {call_connection_id}"
        )
        logger.info(f"CORRELATION ID:--> { event_data['correlationId'] }")
        logger.info(f"CALL CONNECTION ID:--> {event_data['callConnectionId']}")

    async def handle_media_streaming_started(event_data: Dict[str, Any]):
        logger.info(
            f"Media streaming content type:--> {event_data['mediaStreamingUpdate']['contentType']}"
        )
        logger.info(
            f"Media streaming status:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatus']}"
        )
        logger.info(
            f"Media streaming status details:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatusDetails']}"
        )

    async def handle_media_streaming_stopped(event_data: Dict[str, Any]):
        logger.info(
            f"Media streaming content type:--> {event_data['mediaStreamingUpdate']['contentType']}"
        )
        logger.info(
            f"Media streaming status:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatus']}"
        )
        logger.info(
            f"Media streaming status details:--> {event_data['mediaStreamingUpdate']['mediaStreamingStatusDetails']}"
        )

    async def handle_media_streaming_failed(event_data: Dict[str, Any]):
        logger.info(
            f"Code:->{event_data['resultInformation']['code']}, Subcode:-> {event_data['resultInformation']['subCode']}"
        )
        logger.info(f"Message:->{event_data['resultInformation']['message']}")

    async def handle_terminate_call(event_data: Dict[str, Any]):
        call_connection_id = event_data["callConnectionId"]
        try:
            # stop media streaming
            acs_ca_client.get_call_connection(call_connection_id).hang_up(
                is_for_everyone=True
            )
            logger.info(f"Terminated call for connection id: {call_connection_id}")
        except Exception as e:
            logger.error(f"Error stopping media streaming: {e}")
        finally:
            # evict the record from cache
            get_cache_instance().delete(contextId)

    async def handle_transfer_call_to_agent(event_data: Dict[str, Any]):
        call_connection_id = event_data["callConnectionId"]
        try:
            logger.info(
                f"Transfer call to agent event received for connection id: {call_connection_id}"
            )
            # Handle transfer call to agent event
            agent_phone_number = event_data["agentPhoneNumber"]
            acs_phone_number = event_data["acsPhoneNumber"]
            transfer_destination = PhoneNumberIdentifier(agent_phone_number)
            transferee = PhoneNumberIdentifier(acs_phone_number)
            # waiting for 5 seconds before transferring the call
            # This is to ensure that the media streaming is done before transferring the call
            time.sleep(5)
            # Transfer the call to the agent
            result = acs_ca_client.get_call_connection(
                call_connection_id
            ).transfer_call_to_participant(
                target_participant=transfer_destination,
                source_caller_id_number=transferee,
                operation_context="TransferCallToAgent",
                operation_callback_url=get_callback_events_uri() + f"/{contextId}",
            )

            logger.info(
                f"Transfer call to agent initiated for connection id: {call_connection_id}"
            )
        except Exception as e:
            logger.error(f"Error transferring call to agent: {e}")

    event_handlers = {
        "Microsoft.Communication.CallConnected": handle_call_connected,
        "Microsoft.Communication.MediaStreamingStarted": handle_media_streaming_started,
        "Microsoft.Communication.MediaStreamingStopped": handle_media_streaming_stopped,
        "Microsoft.Communication.MediaStreamingFailed": handle_media_streaming_failed,
        "Microsoft.Communication.TerminateCall": handle_terminate_call,
        "Microsoft.Communication.TransferCallToAgent": handle_transfer_call_to_agent,
    }

    for event in await request.json():
        event_data = event["data"]
        call_connection_id = event_data["callConnectionId"]
        event_type = event["type"]

        logger.info(
            f"Received Event:-> {event_type}, Correlation Id:-> {event_data['correlationId']}, CallConnectionId:-> {call_connection_id}"
        )

        handler = event_handlers.get(event_type)
        # setting contextId to event data
        event_data["contextId"] = contextId

        if handler:
            await handler(event_data)
        else:
            logger.info(
                f"Unhandled event type: {event_type}, CallConnectionId: {call_connection_id}"
            )


# WebSocket
@app.websocket("/ws")
async def ws(websocket: WebSocket):
    # Get query parameters from the connection
    query_params = dict(websocket.query_params)
    logger.info(
        f"WebSocket connection established with query parameters: {query_params}"
    )

    print(f"WebSocket connection established with query parameters: {query_params}")
    await websocket.accept()

    service = CommunicationHandler(
        websocket, query_params["uuid"], query_params["acsPhoneNumber"]
    )
    await service.start_conversation_async()

    while True:
        try:
            # Receive data from the client
            data = await websocket.receive_json()
            kind = data["kind"]
            if kind == "AudioData":
                audio_data = data["audioData"]["data"]
                # Send the audio data to the CallAutomationHandler
                await service.send_audio_async(audio_data)
        except Exception as e:
            print(f"WebSocket connection closed: {e}")
            break
