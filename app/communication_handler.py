# Standard library imports
import asyncio
import base64
import json
import os
import uuid
import aiohttp
from typing import List, Dict, Any, Optional

# Third-party imports
from azure.communication.sms import SmsClient, SmsSendResult
from azure.core.credentials import AzureKeyCredential
from azure.identity.aio import DefaultAzureCredential
from dotenv import load_dotenv
from fastapi import WebSocket
from fastapi.websockets import WebSocketState
from openai import AzureOpenAI
from rtclient import (
    FunctionCallOutputItem,
    InputAudioBufferAppendMessage,
    InputAudioTranscription,
    InputTextContentPart,
    ItemCreateMessage,
    RTLowLevelClient,
    ResponseCreateMessage,
    ResponseCreateParams,
    ServerMessageType,
    ServerVAD,
    SessionUpdateMessage,
    SessionUpdateParams,
    UserMessageItem,
)

# Application-specific imports
from app.rag_search_handler import get_search_response
from app.call_router_handler import agent_router_handler, system_blurb_handler, knowledge_base_handler
from app.cache import get_cache as get_cache_instance
from app.unified_logger import get_logger
from app.sms_handler import send_sms

# Initialize logger
logger = get_logger()

AZURE_OPENAI_REALTIME_ENDPOINT = os.getenv("AZURE_OPENAI_REALTIME_ENDPOINT")
AZURE_OPENAI_REALTIME_SERVICE_KEY = os.getenv("AZURE_OPENAI_REALTIME_SERVICE_KEY")
AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME = os.getenv(
    "AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME"
)

ACS_SMS_CONNECTION_STRING = os.getenv("ACS_SMS_CONNECTION_STRING")

load_dotenv()


def get_functions_list() -> List[Dict[str, Any]]:
    curr_dir = os.path.dirname(os.path.abspath(__file__))
    functions_file_path = os.path.join(curr_dir, "functions.json")
    with open(functions_file_path, "r") as file:
        functions_list = json.load(file)
    # print(f"Functions List: {functions_list}")
    return functions_list


class CommunicationHandler:
    voice_name = None or "echo"
    acs_phone_number = None
    customer_call_uuid = None

    def __init__(
        self, websocket: WebSocket, customer_call_uuid: str, acs_number
    ) -> None:
        self.rt_client = None
        self.active_websocket = websocket
        self.customer_call_uuid = customer_call_uuid
        self.acs_phone_number = acs_number
        self.system_prompt = self._build_system_prompt()
        return

    def _build_system_prompt(self) -> str:
        system_blurb = (
            "Cricket Expert"
            if not system_blurb_handler(self.acs_phone_number)
            else system_blurb_handler(self.acs_phone_number)
        )
        return f"""
            You are a {system_blurb}, an AI expert in answering questions based solely on a cricket knowledge base. Your role is to:
            - Provide accurate answers only from the search index using the search tool.
            - Keep responses concise, clear, and friendly, as the user is listening to them over the phone.
            - Follow strict guidelines to ensure accuracy and relevance.
            - Never generate information beyond what is explicitly available in the cricket knowledge base.
            - Don't answer any questions that are not related to the cricket knowledge base.
            - Only answer questions related to the cricket knowledge base — any other questions should be out of scope.
            - Remember: the user is on the phone, so keep your responses brief and clear.

            Conversation Flow:
            1. Greet the user at the start of the session.
            2. Always search the cricket knowledge base before answering any question.
            3. If no relevant information is found, politely say you don't know.
            4. Keep responses brief, preferably in one sentence.
            5. End the conversation only if the user confirms to end the call.

            Guidelines:
            - Do not provide information that is not explicitly found in the search index.
            - Never speculate, assume, or offer personal opinions.
            - Avoid stating or implying that you have general knowledge outside of the cricket knowledge base.
            - Remember, this is a cricket system.
            """

    async def start_conversation_async(self) -> None:
        self.rt_client = RTLowLevelClient(
            url=AZURE_OPENAI_REALTIME_ENDPOINT,
            key_credential=AzureKeyCredential(AZURE_OPENAI_REALTIME_SERVICE_KEY),
            azure_deployment=AZURE_OPENAI_REALTIME_DEPLOYMENT_MODEL_NAME,
        )
        try:
            await self.rt_client.connect()
        except Exception as e:
            logger.info(f"Failed to connect to Azure OpenAI Realtime Service: {e}")
            raise e

        functions = get_functions_list()

        session_update_message = {
            "type": "session.update",
            "session": {
                "voice": "alloy",
                "instructions": self.system_prompt,
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "threshold": 0.6,
                    "silence_duration_ms": 300,
                    "prefix_padding_ms": 200,
                    "type": "server_vad",
                },
                "tools": functions,
            },
        }

        session_update_message_payload = SessionUpdateMessage(**session_update_message)
        await self.rt_client.send(session_update_message_payload)

        # use conversation call_id to track the entire conversation
        self.conversation_call_id = self.customer_call_uuid

        text = (
            "Greet the user with a quick cheery message asking how you can help them."
        )

        await self.send_subscript_message(text)

        asyncio.create_task(self.receive_messages_async())
        return

    async def send_message_async(self, message: str) -> None:
        try:
            if self.active_websocket.client_state == WebSocketState.CONNECTED:
                await self.active_websocket.send_text(message)
        except Exception as e:
            logger.error(f"Send Message - Failed to send message: {e}")
            raise e

    async def receive_messages_async(self) -> None:
        try:
            while not self.rt_client.closed:
                message: ServerMessageType = await self.rt_client.recv()

                if message is None or self.rt_client.ws.closed:
                    continue

                match message.type:
                    case "session.created":
                        logger.info("Session Created Message")
                        logger.info(f"Session Id: {message.session.id}")
                        pass
                    case "error":
                        logger.info(f"Error: {message.error}")
                        pass
                    case "input_audio_buffer.cleared":
                        logger.info("Input Audio Buffer Cleared Message")
                        pass
                    case "input_audio_buffer.speech_started":
                        logger.info(
                            f"Voice activity detection started at {message.audio_start_ms} [ms]"
                        )
                        await self.stop_audio_async()
                        pass
                    case "input_audio_buffer.speech_stopped":
                        pass
                    case "conversation.item.input_audio_transcription.completed":
                        logger.info(f"User:-- {message.transcript}")
                    case "conversation.item.input_audio_transcription.failed":
                        logger.info(f"Error: {message.error}")
                    case "response.done":
                        logger.info("Response Done Message")
                        logger.info(f"  Response Id: {message.response.id}")

                        if message.response.status_details:
                            logger.info(
                                f"Status Details: {message.response.status_details.model_dump_json()}"
                            )
                    case "response.audio_transcript.done":
                        logger.info(f"AI:-- {message.transcript}")
                    case "response.audio.delta":
                        await self.receive_audio(message.delta)
                        pass
                    case "function_call":
                        logger.info(f"Function Call Message: {message}")
                        # Store the original call_id from the function call
                        call_id = message.call_id
                        pass
                    case "response.function_call_arguments.done":
                        logger.info(f"Message: {message}")
                        function_name = message.name
                        args = json.loads(message.arguments)
                        # Use the call_id from the original function call
                        call_id = message.call_id

                        logger.info(f"Function args: {message.arguments}")

                        if function_name == "search":
                            try:
                                user_query = args["query"]
                                search_configuration = knowledge_base_handler(
                                    self.acs_phone_number
                                )

                                # logger.info(f'Search configuration:-- {search_configuration}')

                                search_response = await get_search_response(
                                    {
                                        "query": user_query,
                                        "search_config" : search_configuration
                                    }
                                )
                                
                                if not search_response:
                                    await self.rt_client.ws.send_json(
                                        {
                                            "type": "conversation.item.create",
                                            "item": {
                                                "type": "function_call_output",
                                                "output": "I couldn't find the information requested.",
                                                "call_id": call_id,  # Use original call_id
                                            },
                                        }
                                    )
                                    continue

                                await asyncio.gather(
                                    self.rt_client.ws.send_json(
                                        {
                                            "type": "conversation.item.create",
                                            "item": {
                                                "type": "function_call_output",
                                                "output": f"I have found the insights for query: {search_response}",
                                                "call_id": call_id,  # Use original call_id
                                            },
                                        }
                                    ),
                                    self.rt_client.ws.send_json(
                                        {
                                            "type": "response.create",
                                            "response": {
                                                "modalities": ["text", "audio"],
                                                "instructions": f"Respond to the user as you are speaking on a phone {search_response}. Be concise and friendly.",
                                            },
                                        }
                                    ),
                                )
                            except Exception as e:
                                logger.error(f"Error in recipe search: {e}")
                                await self.rt_client.ws.send_json(
                                    {
                                        "type": "conversation.item.create",
                                        "item": {
                                            "type": "function_call_output",
                                            "output": "Sorry, I encountered an error while searching...",
                                            "call_id": call_id
                                        },
                                    }
                                )

                        elif function_name == "send_sms":
                            # send subscrip message informing the user the message is being sent
                            text = "Inform the user that the message is being sent."
                            await self.send_subscript_message(text)

                            messages = args["messages"]
                            user_metadata = get_cache_instance().get(
                                self.customer_call_uuid
                            )
                            customer_number = user_metadata.get("caller_id", None)
                            await send_sms(messages, customer_number)

                        elif function_name == "transfer_call":
                            text = "Inform the user that the call is being transferred to a human agent."
                            await self.send_subscript_message(text)
                            await asyncio.sleep(4)

                            await self.transfer_to_agent()
                            pass

                        elif function_name == "end_call":
                            user_metadata = get_cache_instance().get(
                                self.customer_call_uuid
                            )
                            if (
                                user_metadata
                                and user_metadata.get("callConnectionId", None)
                                is not None
                            ):
                                # sending message before ending the call
                                text = (
                                    "Inform the user that the call is being terminated."
                                )
                                await self.send_subscript_message(text)
                                await asyncio.sleep(4)

                                # end call
                                await self.terminate_call(user_metadata)

                                # disconnect websocket from server
                                await self.rt_client.close()
                                # clear cache
                                get_cache_instance().delete(self.customer_call_uuid)
                            else:
                                logger.error(
                                    "Call connection ID not found in user metadata."
                                )
                            pass

                        logger.info(f"Function Call Arguments: {message.arguments}")
                        pass
                    case _:
                        pass
        except Exception as e:
            logger.error(f"Error in receive_messages_async: {e}")
            if not isinstance(e, asyncio.CancelledError):
                raise e

    async def receive_audio(self, data_payload) -> None:
        try:
            data_payload = {
                "Kind": "AudioData",
                "AudioData": {"Data": data_payload},
                "StopAudio": None,
            }

            # Serialize the server streaming data
            serialized_data = json.dumps(data_payload)
            await self.send_message_async(serialized_data)

        except Exception as e:
            logger.info(e)

    async def send_audio_async(self, audio_data: str) -> None:
        await self.rt_client.send(
            message=InputAudioBufferAppendMessage(
                type="input_audio_buffer.append", audio=audio_data, _is_azure=True
            )
        )

    async def stop_audio_async(self) -> None:
        try:
            stop_audio_data = {"Kind": "StopAudio", "AudioData": None, "StopAudio": {}}
            json_data = json.dumps(stop_audio_data)
            await self.send_message_async(json_data)
        except Exception as e:
            # logger.info(f"Stop Audio - Failed to send message: {e}")
            logger.error(f"Stop Audio - Failed to send message: {e}")
            raise e
        return

    async def terminate_call(self, user_call_metadata: dict) -> None:
        try:
            from app.main import get_callback_events_uri

            acs_call_uuid = self.customer_call_uuid
            payload = [
                {
                    "type": "Microsoft.Communication.TerminateCall",
                    "data": {
                        "callConnectionId": user_call_metadata["callConnectionId"],
                        "correlationId": user_call_metadata["correlationId"],
                    },
                }
            ]
            remote = get_callback_events_uri()
            url = f"{remote.rstrip('/')}/{acs_call_uuid}"
            logger.info(f"Terminating call with payload: {payload}")
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload) as response:
                    status = response.status
                    text = await response.text()
                    logger.info(
                        f"Terminate call response: Status={status}, Text={text}"
                    )
        except aiohttp.ClientError as e:
            logger.error(f"Failed to terminate call: {e}")
            raise e
        except ValueError as e:
            logger.error(f"Invalid payload or URL format: {e}")
            raise e

    async def send_subscript_message(self, message: str) -> None:
        try:
            content_part = InputTextContentPart(text=message)

            conversation_item = ItemCreateMessage(
                item=UserMessageItem(content=[content_part]),
                call_id=self.customer_call_uuid,  # Use the stored customer_call_id
            )

            await self.rt_client.send(message=conversation_item)

            # NOTE: Need to call this to tell OpenAI to start the conversation and say something first to the user
            await self.rt_client.send(ResponseCreateMessage())
        except Exception as e:
            logger.error(f"Send Message - Failed to send subscript message : {e}")

    async def transfer_to_agent(self) -> None:
        try:
            from app.main import get_callback_events_uri

            callback_uri = get_callback_events_uri()
            uri = f"{callback_uri.rstrip('/')}/{self.customer_call_uuid}"
            agent_number = agent_router_handler(self.acs_phone_number)

            if agent_number is None:
                logger.error(
                    f"Agent number is not found aborting the call transfer {self.customer_call_uuid}."
                )
                return

            # customer metadata
            customer_metadata = get_cache_instance().get(self.customer_call_uuid)
            call_connection, correlation_id = customer_metadata.get(
                "callConnectionId", None
            ), customer_metadata.get("correlationId", None)
            if call_connection is None or correlation_id is None:
                logger.error(
                    f"Call connection ID or correlation ID not found in user metadata unable to transfer the call."
                )
                return

            payload = [
                {
                    "type": "Microsoft.Communication.TransferCallToAgent",
                    "data": {
                        "agentPhoneNumber": agent_number,
                        "callConnectionId": get_cache_instance()
                        .get(self.customer_call_uuid)
                        .get("callConnectionId"),
                        "correlationId": get_cache_instance()
                        .get(self.customer_call_uuid)
                        .get("correlationId"),
                        "acsPhoneNumber": self.acs_phone_number,
                    },
                }
            ]
            # Logic to transfer the call to a human agent
            logger.info("Transferring call to human agent...")

            async with aiohttp.ClientSession() as session:
                async with session.post(uri, json=payload) as response:
                    status = response.status
                    text = await response.text()
                    logger.info(f"Transfer call response: Status={status}, Text={text}")
            pass
        except Exception as e:
            logger.error(f"Error in transferring call: {e}")
