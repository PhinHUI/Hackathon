import os
import datetime
from dotenv import load_dotenv
from portia import (
    Config,
    LLMModel,
    LLMProvider,
    Portia,
    InMemoryToolRegistry,
    Tool,
)
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from ScheduleTool import ScheduleTool

# Load environment variables
load_dotenv()

GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')

# Verify API keys
assert GOOGLE_API_KEY, "GOOGLE_API_KEY is not set"
if not os.getenv("PORTIA_API_KEY"):
    print("Warning: PORTIA_API_KEY not set, cloud features may be unavailable")

# Configure Portia with Google Gemini
google_config = Config.from_default(
    llm_provider=LLMProvider.GOOGLE_GENERATIVE_AI,
    llm_model_name=LLMModel.GEMINI_2_0_FLASH,
    google_api_key=GOOGLE_API_KEY
)

# Initialize tool registry
tool_registry = InMemoryToolRegistry()

# Mock patient requests
requests = [
    {"patient": "John Doe", "condition": "chest pain", "urgency": "urgent", "email": "john@example.com", "timestamp": "2025-04-12T08:00:00"},
    {"patient": "Jane Smith", "condition": "annual checkup", "urgency": "routine", "email": "jane@example.com", "timestamp": "2025-04-12T08:05:00"},
    {"patient": "Bob Lee", "condition": "diabetes follow-up", "urgency": "moderate", "email": "bob@example.com", "timestamp": "2025-04-12T08:10:00"}
]

# Prioritization logic
def prioritize_requests(requests):
    urgency_scores = {"urgent": 3, "moderate": 2, "routine": 1}
    for req in requests:
        req["score"] = urgency_scores.get(req["urgency"], 1)
    return sorted(requests, key=lambda x: (x["score"], x["timestamp"]), reverse=True)

# Mock calendar
calendar_slots = []
def schedule_appointment(patient, urgency, condition):
    now = datetime.datetime.now()
    if urgency == "urgent":
        start_time = now + datetime.timedelta(hours=1)
    elif urgency == "moderate":
        start_time = now + datetime.timedelta(days=1)
    else:
        start_time = now + datetime.timedelta(days=3)
    slot = {
        "patient": patient,
        "condition": condition,
        "start_time": start_time.isoformat(),
        "end_time": (start_time + datetime.timedelta(minutes=30)).isoformat()
    }
    calendar_slots.append(slot)
    return slot

# Google Calendar API setup
def get_calendar_service():
    flow = InstalledAppFlow.from_client_secrets_file(
        os.getenv("GOOGLE_APPLICATION_CREDENTIALS"),
        scopes=["https://www.googleapis.com/auth/calendar"]
    )
    creds = flow.run_local_server(port=0)
    return build("calendar", "v3", credentials=creds)

# Mock email function
def send_email(to, subject, body):
    print(f"Mock email sent to {to}: Subject: {subject}, Body: {body}")
    return {"status": "sent"}

# Custom scheduler tool
def custom_scheduler(requests):
    prioritized = prioritize_requests(requests)
    appointments = []
    for req in prioritized:
        slot = schedule_appointment(req["patient"], req["urgency"], req["condition"])
        appointments.append({
            "patient": req["patient"],
            "email": req["email"],
            "slot": slot
        })
    return appointments

tool = ScheduleTool()
# Register custom tool
tool_registry.register_tool(tool)

# Initialize Portia
portia = Portia(config=google_config, tools=tool_registry)

# Plan to summarize and schedule
plan = {
    "steps": [
        {
            "task": "Prioritize patient appointment requests based on urgency.",
            "inputs": [
                {
                    "name": "$requests",
                    "value": requests,
                    "description": "List of patient appointment requests."
                }
            ],
            "tool_id": "llm_tool",
            "output": "$prioritized_requests",
            "description": "Use LLM to confirm urgency scores align with medical context."
        },
        {
            "task": "Schedule appointments for prioritized patients.",
            "inputs": [
                {
                    "name": "$prioritized_requests",
                    "description": "Prioritized list of appointment requests."
                }
            ],
            "tool_id": "custom_scheduler",
            "output": "$scheduled_appointments",
            "description": "Assign calendar slots based on urgency."
        },
        {
            "task": "Email patients their appointment confirmations.",
            "inputs": [
                {
                    "name": "$scheduled_appointments",
                    "description": "List of scheduled appointments."
                }
            ],
            "tool_id": "portia:google:gmail:send_email",
            "output": "$email_confirmations",
            "description": "Send confirmation emails to patients."
        }
    ]
}

# Execute plan manually (bypassing Portia plan execution to avoid unrelated query)
try:
    # Step 1: Prioritize (mock LLM step)
    prioritized = prioritize_requests(requests)
    print("Prioritized Requests:", prioritized)

    # Step 2: Schedule
    appointments = custom_scheduler(prioritized)
    print("Scheduled Appointments:", appointments)

    # Step 3: Email
    confirmations = []
    for appt in appointments:
        body = f"Dear {appt['patient']},\nYour appointment is scheduled for {appt['slot']['start_time']}.\nReason: {appt['slot']['condition']}"
        result = send_email(appt["email"], "Appointment Confirmation", body)
        confirmations.append(result)
    print("Email Confirmations:", confirmations)

except Exception as e:
    print(f"Error: {e}")