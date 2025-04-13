import os
import datetime
import uuid
from dotenv import load_dotenv
from portia import (
    Config,
    LLMModel,
    LLMProvider,
    Portia,
    InMemoryToolRegistry,
    ToolRunContext,
)
from portia.errors import ToolHardError, ToolSoftError
from ScheduleTool import ScheduleTool
from EmailTool import EmailTool

# Load environment variables
load_dotenv()

GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")

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

# Mock patient requests
requests = [
    {"patient": "John Doe", "condition": "chest pain", "urgency": "urgent", "email": "jimstse@gmail.com", "timestamp": "2025-04-12T08:00:00"},
    {"patient": "Jane Smith", "condition": "annual checkup", "urgency": "routine", "email": "jane@example.com", "timestamp": "2025-04-12T08:05:00"},
    {"patient": "Bob Lee", "condition": "diabetes follow-up", "urgency": "moderate", "email": "bob@example.com", "timestamp": "2025-04-12T08:10:00"}
]

# Prioritization logic
def prioritize_requests(requests):
    urgency_scores = {"urgent": 3, "moderate": 2, "routine": 1}
    for req in requests:
        req["score"] = urgency_scores.get(req["urgency"], 1)
    return sorted(requests, key=lambda x: (x["score"], x["timestamp"]), reverse=True)

# Initialize tools
schedule_tool = ScheduleTool()
email_tool = EmailTool()

# Initialize tool registry
tool_registry = InMemoryToolRegistry()
tool_registry.register_tool(schedule_tool)
tool_registry.register_tool(email_tool)

# Initialize Portia
portia = Portia(config=google_config, tools=tool_registry)

# Process appointments
def process_appointments(requests):
    prioritized = prioritize_requests(requests)
    appointments = []
    # Initialize ToolRunContext with a valid plan_run_id prefixed with "prun-"
    context = ToolRunContext(
        execution_context={"user": "system", "session": "default"},
        plan_run_id=f"prun-{uuid.uuid4()}",  # Prepend "prun-" to the UUID
        config=google_config,
        clarifications=[]
    )

    for req in prioritized:
        try:
            # Determine appointment date based on urgency
            now = datetime.datetime.now()
            if req["urgency"] == "urgent":
                appt_date = (now + datetime.timedelta(hours=1)).strftime("%Y-%m-%d")
            elif req["urgency"] == "moderate":
                appt_date = (now + datetime.timedelta(days=1)).strftime("%Y-%m-%d")
            else:
                appt_date = (now + datetime.timedelta(days=3)).strftime("%Y-%m-%d")

            # Schedule using ScheduleTool
            schedule_result = schedule_tool.run(context, appt_date)
            slot = {
                "patient": req["patient"],
                "condition": req["condition"],
                "start_time": f"{appt_date}T10:00:00",
                "end_time": f"{appt_date}T11:00:00",
                "event_id": schedule_result.split("Event ID: ")[1]
            }

            # Send email using EmailTool
            email_body = (
                f"Dear {req['patient']},\n"
                f"Your appointment is scheduled for {slot['start_time']}.\n"
                f"Reason: {req['condition']}\n"
                f"Best regards,\nYour Clinic"
            )
            email_result = email_tool.run(context, req["email"])

            appointments.append({
                "patient": req["patient"],
                "email": req["email"],
                "slot": slot,
                "email_status": email_result
            })

        except ToolHardError as e:
            print(f"Critical error for {req['patient']}: {e}")
            continue
        except ToolSoftError as e:
            print(f"Recoverable error for {req['patient']}: {e}")
            continue
        except Exception as e:
            print(f"Unexpected error for {req['patient']}: {e}")
            continue

    return appointments

# Execute
try:
    # Step 1: Prioritize
    print("Prioritizing requests...")
    prioritized = prioritize_requests(requests)
    print("Prioritized Requests:", prioritized)

    # Step 2: Process appointments (schedule and email)
    print("Scheduling and emailing...")
    appointments = process_appointments(prioritized)
    print("Scheduled Appointments:", appointments)

except Exception as e:
    print(f"Error: {e}")