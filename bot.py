#
# Copyright (c) 2024-2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Pipecat Quickstart Example.

The example runs a simple voice AI bot that you can connect to using your
browser and speak with it. You can also deploy this bot to Pipecat Cloud.

Required AI services:
- ElevenLabs (Speech-to-Text and Text-to-Speech)
- OpenAI (LLM)

Run the bot using::

    uv run bot.py
"""

import os

from dotenv import load_dotenv
from loguru import logger
from healthie import create_appointment, find_patient, login_to_healthie

print("🚀 Starting Pipecat bot...")
print("⏳ Loading models and imports (20 seconds, first run only)\n")

logger.info("Loading Local Smart Turn Analyzer V3...")
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

logger.info("✅ Local Smart Turn Analyzer V3 loaded")
logger.info("Loading Silero VAD model...")
from pipecat.audio.vad.silero import SileroVADAnalyzer

logger.info("✅ Silero VAD model loaded")

from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import LLMRunFrame

logger.info("Loading pipeline components...")
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.runner import PipelineRunner
from pipecat.pipeline.task import PipelineParams, PipelineTask
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frameworks.rtvi import RTVIObserver, RTVIProcessor
from pipecat.runner.types import RunnerArguments
from pipecat.runner.utils import create_transport
from pipecat.services.elevenlabs.stt import ElevenLabsRealtimeSTTService
from pipecat.services.elevenlabs.tts import ElevenLabsTTSService
from pipecat.services.llm_service import FunctionCallParams
from pipecat.services.openai.llm import OpenAILLMService
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.turns.user_start import VADUserTurnStartStrategy
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.turns.user_turn_strategies import UserTurnStrategies

logger.info("✅ All components loaded successfully!")

load_dotenv(override=True)


async def run_bot(transport: BaseTransport, runner_args: RunnerArguments):
    logger.info(f"Starting bot")

    elevenlabs_key = os.environ["ELEVENLABS_API_KEY"]
    stt = ElevenLabsRealtimeSTTService(api_key=elevenlabs_key)
    tts = ElevenLabsTTSService(
        api_key=elevenlabs_key,
        voice_id="SAz9YHcvj6GT2YYXdXww",
    )

    llm = OpenAILLMService(api_key=os.environ["OPENAI_API_KEY"])

    async def find_patient_handler(params: FunctionCallParams):
        try:
            name = str(params.arguments.get("name", "")).strip()
            date_of_birth = str(params.arguments.get("date_of_birth", "")).strip()
            if not name or not date_of_birth:
                await params.result_callback(
                    {"error": "Missing required fields: name and date_of_birth are required."}
                )
                return

            patient = await find_patient(name, date_of_birth)
            if not patient:
                await params.result_callback(
                    {
                        "found": False,
                        "message": f"No patient found for name '{name}' and date of birth '{date_of_birth}'.",
                    }
                )
                return

            await params.result_callback({"found": True, **patient})
        except Exception as exc:
            logger.exception("find_patient function handler failed: {}", exc)
            await params.result_callback(
                {
                    "found": False,
                    "error": "Unable to access Healthie right now. Please confirm your details and try again.",
                }
            )

    async def create_appointment_handler(params: FunctionCallParams):
        try:
            patient_id = str(params.arguments.get("patient_id", "")).strip()
            date = str(params.arguments.get("date", "")).strip()
            time = str(params.arguments.get("time", "")).strip()
            appointment_type = str(params.arguments.get("appointment_type", "")).strip()
            if not patient_id or not date or not time or not appointment_type:
                await params.result_callback(
                    {
                        "error": (
                            "Missing required fields: patient_id, date, time, and "
                            "appointment_type (initial_consultation or follow_up) are required."
                        )
                    }
                )
                return

            appointment = await create_appointment(
                patient_id, date, time, appointment_type=appointment_type
            )
            if not appointment:
                await params.result_callback(
                    {
                        "created": False,
                        "message": "Unable to create appointment. The date/time may be unavailable.",
                    }
                )
                return

            await params.result_callback({"created": True, **appointment})
        except Exception as exc:
            logger.exception("create_appointment function handler failed: {}", exc)
            await params.result_callback(
                {
                    "created": False,
                    "error": "Unable to schedule right now due to a system issue. Please try again.",
                }
            )

    llm.register_function("find_patient", find_patient_handler)
    llm.register_function("create_appointment", create_appointment_handler)

    tools = ToolsSchema(
        standard_tools=[
            FunctionSchema(
                name="find_patient",
                description="Find an existing patient in Healthie by full name and date of birth.",
                properties={
                    "name": {"type": "string", "description": "Patient full name."},
                    "date_of_birth": {
                        "type": "string",
                        "description": "Patient date of birth, preferably YYYY-MM-DD.",
                    },
                },
                required=["name", "date_of_birth"],
            ),
            FunctionSchema(
                name="create_appointment",
                description=(
                    "Create a new appointment in Healthie for an existing patient. "
                    "Requires the visit type the caller chose (initial vs follow-up)."
                ),
                properties={
                    "patient_id": {"type": "string", "description": "Healthie patient id."},
                    "date": {
                        "type": "string",
                        "description": "Appointment date, preferably YYYY-MM-DD.",
                    },
                    "time": {
                        "type": "string",
                        "description": "Appointment time (for example, 10:30 AM).",
                    },
                    "appointment_type": {
                        "type": "string",
                        "enum": ["initial_consultation", "follow_up"],
                        "description": (
                            "initial_consultation = first visit, 60 minutes (Initial Consultation). "
                            "follow_up = follow-up visit, 45 minutes (Follow-up Session). "
                            "Must match what the caller explicitly chose."
                        ),
                    },
                },
                required=["patient_id", "date", "time", "appointment_type"],
            ),
        ]
    )

    messages = [
        {
            "role": "system",
            "content": (
                "You are a scheduling assistant for Prosper Health clinic. "
                "Keep replies short and conversational for voice. "
                "Collect patient full name and date of birth first. "
                "Then call the find_patient tool. "
                "If found, ask for (1) whether this is an initial visit (60 minutes) or a follow-up "
                "(45 minutes), (2) desired appointment date, and (3) time. "
                "Do not call create_appointment until the caller has clearly chosen initial vs follow-up. "
                "When you have date, time, and appointment_type, call create_appointment immediately "
                "with patient_id from the last successful find_patient result, before any long reply. "
                "Confirm success clearly with the user. "
                "If a tool returns an error or not found, explain it and ask for corrected information."
            ),
        },
    ]

    context = LLMContext(messages, tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                # Default also enables TranscriptionUserTurnStartStrategy, which fires on
                # interim STT and interrupts the LLM mid-tool-call. VAD-only avoids that.
                start=[VADUserTurnStartStrategy()],
                stop=[TurnAnalyzerUserTurnStopStrategy(turn_analyzer=LocalSmartTurnAnalyzerV3())],
            ),
        ),
    )

    rtvi = RTVIProcessor()

    pipeline = Pipeline(
        [
            transport.input(),  # Transport user input
            rtvi,  # RTVI processor
            stt,
            user_aggregator,  # User responses
            llm,  # LLM
            tts,  # TTS
            transport.output(),  # Transport bot output
            assistant_aggregator,  # Assistant spoken responses
        ]
    )

    task = PipelineTask(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
        ),
        observers=[RTVIObserver(rtvi)],
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info(f"Client connected")
        try:
            # Pre-warm Healthie auth at session start to reduce first tool-call latency/failure.
            await login_to_healthie()
            logger.info("Healthie session pre-initialized for this call")
        except Exception as exc:
            logger.warning("Healthie pre-login failed (will retry during tool call): {}", exc)
        # Kick off the conversation.
        messages.append(
            {
                "role": "system",
                "content": (
                    "Start by greeting the caller and ask for their full name and date of birth "
                    "to help schedule an appointment."
                ),
            }
        )
        await task.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        logger.info(f"Client disconnected")
        await task.cancel()

    runner = PipelineRunner(handle_sigint=runner_args.handle_sigint)

    await runner.run(task)


async def bot(runner_args: RunnerArguments):
    """Main bot entry point for the bot starter."""

    transport_params = {
        "webrtc": lambda: TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.35)),
        ),
    }

    transport = await create_transport(runner_args, transport_params)

    await run_bot(transport, runner_args)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
