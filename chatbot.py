import tkinter as tk
from tkinter import ttk, messagebox
from datetime import datetime, timedelta
import os
import httpx
import google.generativeai as genai
from pydantic import BaseModel, Field
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from portia import (
    Config,
    LLMModel,
    LLMProvider,
    Portia,
    InMemoryToolRegistry,
    Tool,
    ToolRunContext,
)
from portia.errors import ToolHardError, ToolSoftError
from dotenv import load_dotenv
import logging
import base64
from email.mime.text import MIMEText

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

load_dotenv()
GOOGLE_API_KEY = os.getenv('GOOGLE_API_KEY')
assert GOOGLE_API_KEY, "GOOGLE_API_KEY is not set"

genai.configure(api_key="AIzaSyDkEmGFRmCTbv1tSYPTw0zoCRRhQQ5CvTk")
gemini_model = genai.GenerativeModel('gemini-1.5-flash')

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_PATH = os.path.join(BASE_DIR, "credentials.json")
TOKEN_PATH = os.path.join(BASE_DIR, "token.json")

requests = [
    {"patient": "John Doe", "condition": "chest pain", "urgency": "urgent", "email": "jimstse@gmail.com", "timestamp": "2025-04-12T08:00:00"},
    {"patient": "Jane Smith", "condition": "annual checkup", "urgency": "routine", "email": "jane@example.com", "timestamp": "2025-04-12T08:05:00"},
    {"patient": "Bob Lee", "condition": "diabetes follow-up", "urgency": "moderate", "email": "bob@example.com", "timestamp": "2025-04-12T08:10:00"}
]

def prioritize_requests(requests):
    urgency_scores = {"urgent": 3, "moderate": 2, "routine": 1}
    for req in requests:
        req["score"] = urgency_scores.get(req["urgency"], 1)
    return sorted(requests, key=lambda x: (x["score"], x["timestamp"]), reverse=True)

class ScheduleToolSchema(BaseModel):
    date: str = Field(..., description="The date to set the schedule (format: YYYY-MM-DD)")
    patient: str = Field(..., description="Patient name")
    condition: str = Field(..., description="Reason for appointment")

class ScheduleTool(Tool[str]):
    id: str = "schedule_tool"
    name: str = "Scheduler"
    description: str = "Schedule appointments on Google Calendar"
    args_schema: type[BaseModel] = ScheduleToolSchema
    output_schema: tuple[str, str] = ("str", "String output of the scheduled appointment")

    def _get_calendar_service(self):
        SCOPES = ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/gmail.send"]
        creds = None
        try:
            logger.info(f"Checking for token.json at: {TOKEN_PATH}")
            if os.path.exists(TOKEN_PATH):
                creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
                logger.info("Loaded token.json")
                # Verify all required scopes are present
                if not all(scope in creds.scopes for scope in SCOPES):
                    logger.warning("Token missing required scopes; re-authenticating")
                    creds = None
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    logger.info("Refreshing expired token")
                    creds.refresh(httpx.Request())
                else:
                    logger.info(f"Checking for credentials.json at: {CREDENTIALS_PATH}")
                    if not os.path.exists(CREDENTIALS_PATH):
                        error_msg = (
                            f"Missing credentials.json at {CREDENTIALS_PATH}. "
                            "Please download it from Google Cloud Console and place it in the project directory. "
                            "See https://developers.google.com/calendar/api/quickstart/python for details."
                        )
                        logger.error(error_msg)
                        raise ToolHardError(error_msg)
                    logger.info("Initiating OAuth flow with credentials.json")
                    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                    creds = flow.run_local_server(port=0)
                    logger.info(f"Saving new token to: {TOKEN_PATH}")
                    with open(TOKEN_PATH, "w") as token:
                        token.write(creds.to_json())
            return build("calendar", "v3", credentials=creds)
        except Exception as e:
            logger.error(f"Authentication failed: {str(e)}")
            raise ToolHardError(f"Failed to authenticate with Google Calendar: {str(e)}")

    def run(self, _: ToolRunContext, date: str, patient: str, condition: str) -> str:
        try:
            try:
                event_date = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                logger.error(f"Invalid date format: {date}")
                raise ToolSoftError(f"Invalid date format: {date}. Use YYYY-MM-DD")

            service = self._get_calendar_service()

            start_time = event_date.replace(hour=10, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%SZ")
            end_time = event_date.replace(hour=11, minute=0, second=0).strftime("%Y-%m-%dT%H:%M:%SZ")

            event = {
                "summary": f"Appointment for {patient}",
                "description": f"Condition: {condition}",
                "start": {
                    "dateTime": start_time,
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": end_time,
                    "timeZone": "UTC",
                },
            }

            logger.info(f"Creating calendar event for {patient} on {date}: {event}")
            created_event = service.events().insert(calendarId="primary", body=event).execute()

            return f"Scheduled appointment for {patient} on {date} at 10:00 UTC. Event ID: {created_event['id']}"

        except HttpError as error:
            logger.error(f"Google Calendar API error: {error}")
            raise ToolSoftError(f"Failed to schedule event: {error}")
        except Exception as e:
            logger.error(f"Unexpected error in ScheduleTool: {str(e)}")
            raise ToolHardError(f"An unexpected error occurred: {str(e)}")

class EmailToolSchema(BaseModel):
    to: str = Field(..., description="Recipient email address")
    subject: str = Field(..., description="Email subject")
    body: str = Field(..., description="Email body content")

class EmailTool(Tool[str]):
    id: str = "email_tool"
    name: str = "Email Sender"
    description: str = "Send emails using Gmail API"
    args_schema: type[BaseModel] = EmailToolSchema
    output_schema: tuple[str, str] = ("str", "String output of the email sending result")

    def _get_gmail_service(self):
        SCOPES = ["https://www.googleapis.com/auth/calendar", "https://www.googleapis.com/auth/gmail.send"]
        creds = None
        try:
            logger.info(f"Checking for token.json at: {TOKEN_PATH}")
            if os.path.exists(TOKEN_PATH):
                creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
                logger.info("Loaded token.json")
                # Verify all required scopes are present
                if not all(scope in creds.scopes for scope in SCOPES):
                    logger.warning("Token missing required scopes; re-authenticating")
                    creds = None
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    logger.info("Refreshing expired token")
                    creds.refresh(httpx.Request())
                else:
                    logger.info(f"Checking for credentials.json at: {CREDENTIALS_PATH}")
                    if not os.path.exists(CREDENTIALS_PATH):
                        error_msg = (
                            f"Missing credentials.json at {CREDENTIALS_PATH}. "
                            "Please download it from Google Cloud Console and place it in the project directory. "
                            "See https://developers.google.com/gmail/api/quickstart/python for details."
                        )
                        logger.error(error_msg)
                        raise ToolHardError(error_msg)
                    logger.info("Initiating OAuth flow with credentials.json")
                    flow = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_PATH, SCOPES)
                    creds = flow.run_local_server(port=0)
                    logger.info(f"Saving new token to: {TOKEN_PATH}")
                    with open(TOKEN_PATH, "w") as token:
                        token.write(creds.to_json())
            return build("gmail", "v1", credentials=creds)
        except Exception as e:
            logger.error(f"Authentication failed for Gmail: {str(e)}")
            raise ToolHardError(f"Failed to authenticate with Gmail: {str(e)}")

    def run(self, _: ToolRunContext, to: str, subject: str, body: str) -> str:
        try:
            service = self._get_gmail_service()
            message = MIMEText(body)
            message['to'] = to
            message['subject'] = subject
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            logger.info(f"Sending email to {to}")
            service.users().messages().send(userId="me", body={"raw": raw_message}).execute()
            return f"Email sent to {to}"
        except HttpError as error:
            logger.error(f"Gmail API error: {error}")
            raise ToolSoftError(f"Failed to send email: {error}")
        except Exception as e:
            logger.error(f"Unexpected error in EmailTool: {str(e)}")
            raise ToolHardError(f"An unexpected error occurred: {str(e)}")

class RequestManagerSchema(BaseModel):
    action: str = Field(..., description="Action to perform: add, prioritize, or list")
    patient: str | None = Field(None, description="Patient name (required for add)")
    condition: str | None = Field(None, description="Condition (required for add)")
    urgency: str | None = Field(None, description="Urgency level: urgent, moderate, routine (required for add)")
    email: str | None = Field(None, description="Patient email (required for add)")

class RequestManagerTool(Tool[str]):
    id: str = "request_manager"
    name: str = "Request Manager"
    description: str = "Manage patient appointment requests"
    args_schema: type[BaseModel] = RequestManagerSchema
    output_schema: tuple[str, str] = ("str", "String output of request actions")

    def run(self, _: ToolRunContext, action: str, patient: str | None = None, condition: str | None = None, urgency: str | None = None, email: str | None = None) -> str:
        global requests
        if action == "add":
            if not all([patient, condition, urgency, email]):
                logger.error("Missing required fields for add action")
                raise ToolSoftError("Missing required fields for add action")
            new_request = {
                "patient": patient,
                "condition": condition,
                "urgency": urgency,
                "email": email,
                "timestamp": datetime.now().isoformat()
            }
            requests.append(new_request)
            logger.info(f"Added request for {patient}")
            return f"Added request for {patient}"
        elif action == "prioritize":
            prioritized = prioritize_requests(requests)
            logger.info("Prioritized requests")
            return f"Prioritized requests: {[r['patient'] for r in prioritized]}"
        elif action == "list":
            logger.info("Listed requests")
            return f"Current requests: {[r['patient'] for r in requests]}"
        else:
            logger.error(f"Unknown action: {action}")
            raise ToolSoftError(f"Unknown action: {action}")

# Chatbot UI
class ChatbotUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Portia AI Medical Assistant")
        self.root.geometry("800x600")

        self.google_config = Config.from_default(
            llm_provider=LLMProvider.GOOGLE_GENERATIVE_AI,
            llm_model_name=LLMModel.GEMINI_2_0_FLASH,
            google_api_key=GOOGLE_API_KEY
        )
        self.tool_registry = InMemoryToolRegistry()
        self.tool_registry.register_tool(ScheduleTool())
        self.tool_registry.register_tool(RequestManagerTool())
        self.tool_registry.register_tool(EmailTool())
        self.portia = Portia(config=self.google_config, tools=self.tool_registry)

        self.appointments = []
        self.confirmations = []

        self.create_widgets()

    def create_widgets(self):
        chat_frame = ttk.Frame(self.root, padding="10")
        chat_frame.grid(row=0, column=0, sticky="nsew")
        results_frame = ttk.Frame(self.root, padding="10")
        results_frame.grid(row=0, column=1, sticky="nsew")

        self.root.columnconfigure(0, weight=2)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(0, weight=1)

        self.create_chat_panel(chat_frame)
        self.create_results_panel(results_frame)

    def create_chat_panel(self, parent):
        chat_frame = ttk.LabelFrame(parent, text="Chat with Portia AI", padding="5")
        chat_frame.pack(fill="both", expand=True)

        self.chat_output = tk.Text(chat_frame, height=20, state="disabled", wrap="word")
        self.chat_output.pack(fill="both", expand=True, padx=5, pady=5)

        input_frame = ttk.Frame(chat_frame)
        input_frame.pack(fill="x", pady=5)
        self.chat_input = ttk.Entry(input_frame)
        self.chat_input.pack(side="left", fill="x", expand=True, padx=5)
        ttk.Button(input_frame, text="Send", command=self.send_message).pack(side="right")
        self.chat_input.bind("<Return>", lambda event: self.send_message())

    def create_results_panel(self, parent):
        results_frame = ttk.LabelFrame(parent, text="Results", padding="5")
        results_frame.pack(fill="both", expand=True)

        notebook = ttk.Notebook(results_frame)
        notebook.pack(fill="both", expand=True)

        self.requests_frame = ttk.Frame(notebook)
        notebook.add(self.requests_frame, text="Requests")
        self.requests_tree = self.create_treeview(self.requests_frame, ["Patient", "Condition", "Urgency", "Email", "Timestamp"])
        self.update_requests_tree()

        self.appointments_frame = ttk.Frame(notebook)
        notebook.add(self.appointments_frame, text="Appointments")
        self.appointments_tree = self.create_treeview(self.appointments_frame, ["Patient", "Condition", "Start Time", "Event ID"])
        self.update_appointments_tree()

        self.emails_frame = ttk.Frame(notebook)
        notebook.add(self.emails_frame, text="Emails")
        self.emails_tree = self.create_treeview(self.emails_frame, ["Email", "Status"])
        self.update_emails_tree()

    def create_treeview(self, parent, columns):
        tree = ttk.Treeview(parent, columns=columns, show="headings")
        for col in columns:
            tree.heading(col, text=col)
            tree.column(col, width=100)
        tree.pack(fill="both", expand=True, padx=5, pady=5)
        return tree

    def send_message(self):
        user_input = self.chat_input.get().strip()
        if not user_input:
            return

        self.display_message(f"You: {user_input}")
        self.chat_input.delete(0, tk.END)

        try:
            response = self.process_user_input(user_input)
            self.display_message(f"Portia: {response}")
        except Exception as e:
            logger.error(f"Error processing input: {str(e)}")
            self.display_message(f"Portia: Error: {str(e)}")

    def display_message(self, message):
        self.chat_output.config(state="normal")
        self.chat_output.insert(tk.END, message + "\n")
        self.chat_output.see(tk.END)
        self.chat_output.config(state="disabled")

    def process_user_input(self, user_input):
        global requests
        plan = {"steps": []}

        user_input_lower = user_input.lower()
        if "add request" in user_input_lower or "book appointment" in user_input_lower:
            try:
                parts = user_input_lower.split(",")
                patient = parts[0].split("for")[-1].strip()
                condition = parts[1].strip() if len(parts) > 1 else "unknown"
                urgency = parts[2].strip() if len(parts) > 2 else "routine"
                email = parts[3].split("email")[-1].strip() if len(parts) > 3 else "unknown@example.com"

                plan["steps"].append({
                    "task": "Add patient request",
                    "inputs": [
                        {"name": "action", "value": "add"},
                        {"name": "patient", "value": patient},
                        {"name": "condition", "value": condition},
                        {"name": "urgency", "value": urgency},
                        {"name": "email", "value": email}
                    ],
                    "tool_id": "request_manager",
                    "output": "$request_added",
                    "description": "Add a new patient request"
                })
            except:
                logger.warning(f"Invalid format for add request: {user_input}")
                return "Please provide details like: book appointment for [name] with [condition], [urgency], email [email]"

        elif "prioritize" in user_input_lower:
            plan["steps"].append({
                "task": "Prioritize requests",
                "inputs": [{"name": "action", "value": "prioritize"}],
                "tool_id": "request_manager",
                "output": "$prioritized",
                "description": "Prioritize patient requests"
            })

        elif "schedule" in user_input_lower:
            prioritized = prioritize_requests(requests)
            for req in prioritized:
                date = (datetime.now() + timedelta(days=1 if req["urgency"] == "moderate" else 3 if req["urgency"] == "routine" else 0)).strftime("%Y-%m-%d")
                plan["steps"].append({
                    "task": f"Schedule appointment for {req['patient']}",
                    "inputs": [
                        {"name": "date", "value": date},
                        {"name": "patient", "value": req["patient"]},
                        {"name": "condition", "value": req["condition"]}
                    ],
                    "tool_id": "schedule_tool",
                    "output": f"$appointment_{req['patient']}",
                    "description": f"Schedule appointment for {req['patient']}"
                })

        elif "send email" in user_input_lower:
            for appt in self.appointments:
                plan["steps"].append({
                    "task": f"Send email to {appt['email']}",
                    "inputs": [
                        {"name": "to", "value": appt['email']},
                        {"name": "subject", "value": "Appointment Confirmation"},
                        {"name": "body", "value": f"Dear {appt['patient']},\nYour appointment is scheduled for {appt['start_time']}.\nReason: {appt['condition']}\nBest regards,\nYour Clinic"}
                    ],
                    "tool_id": "email_tool",
                    "output": f"$email_{appt['email']}",
                    "description": f"Send confirmation email to {appt['email']}"
                })

        if plan["steps"]:
            results = []
            for step in plan["steps"]:
                tool_id = step["tool_id"]
                inputs = {inp["name"]: inp["value"] for inp in step["inputs"]}
                try:
                    if tool_id == "schedule_tool":
                        result = self.tool_registry.get_tool(tool_id).run(None, **inputs)
                        self.appointments.append({
                            "patient": inputs["patient"],
                            "condition": inputs["condition"],
                            "start_time": f"{inputs['date']}T10:00:00",
                            "email": next((r["email"] for r in requests if r["patient"] == inputs["patient"]), "unknown@example.com"),
                            "event_id": result.split("Event ID: ")[-1]
                        })
                        self.update_appointments_tree()
                        results.append(result)

                    elif tool_id == "request_manager":
                        result = self.tool_registry.get_tool(tool_id).run(None, **inputs)
                        self.update_requests_tree()
                        results.append(result)

                    elif tool_id == "email_tool":
                        result = self.tool_registry.get_tool(tool_id).run(None, **inputs)
                        self.confirmations.append({"to": inputs["to"], "status": "sent"})
                        self.update_emails_tree()
                        results.append(result)
                except Exception as e:
                    logger.error(f"Error in {step['task']}: {str(e)}")
                    results.append(f"Error in {step['task']}: {str(e)}")
            return "\n".join(results)
        else:
            try:
                logger.info(f"Sending general query to Gemini: {user_input}")
                response = gemini_model.generate_content(user_input)
                return response.text
            except Exception as e:
                logger.error(f"Failed to process Gemini query: {str(e)}")
                return f"Failed to process query: {str(e)}"

    def update_requests_tree(self):
        for item in self.requests_tree.get_children():
            self.requests_tree.delete(item)
        for req in requests:
            self.requests_tree.insert("", tk.END, values=(
                req["patient"], req["condition"], req["urgency"], req["email"], req["timestamp"]
            ))

    def update_appointments_tree(self):
        for item in self.appointments_tree.get_children():
            self.appointments_tree.delete(item)
        for appt in self.appointments:
            self.appointments_tree.insert("", tk.END, values=(
                appt["patient"], appt["condition"], appt["start_time"], appt["event_id"]
            ))

    def update_emails_tree(self):
        for item in self.emails_tree.get_children():
            self.emails_tree.delete(item)
        for conf in self.confirmations:
            self.emails_tree.insert("", tk.END, values=(
                conf.get("to", "unknown"), conf["status"]
            ))

def main():
    logger.info(f"Starting application from directory: {BASE_DIR}")
    root = tk.Tk()
    app = ChatbotUI(root)
    root.mainloop()

if __name__ == "__main__":
    main()
