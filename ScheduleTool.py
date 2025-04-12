import os
from datetime import datetime, timedelta
import httpx
from pydantic import BaseModel, Field
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from portia.errors import ToolHardError, ToolSoftError
from portia.tool import Tool, ToolRunContext

class ScheduleToolSchema(BaseModel):
    date: str = Field(..., description="The date to set the schedule (format: YYYY-MM-DD)")

class ScheduleTool(Tool[str]):
    id: str = "schedule_tool"
    name: str = "Scheduler"
    description: str = "Schedule appointments"
    args_schema: type[BaseModel] = ScheduleToolSchema
    output_schema: tuple[str, str] = ("str", "String output of the date scheduled")

    def _get_calendar_service(self):
        """Set up and return Google Calendar API service."""
        SCOPES = ["https://www.googleapis.com/auth/calendar"]
        creds = None
        # Check for existing credentials
        if os.path.exists("token.json"):
            creds = Credentials.from_authorized_user_file("token.json", SCOPES)
        # If no valid credentials, prompt user to log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(httpx.Request())
            else:
                if not os.path.exists("credentials.json"):
                    raise ToolHardError("credentials.json is required for Google Calendar API")
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
                # Save credentials for future runs
                with open("token.json", "w") as token:
                    token.write(creds.to_json())
        return build("calendar", "v3", credentials=creds)

    def run(self, _: ToolRunContext, date: str) -> str:
        """Run the Scheduler to create a Google Calendar event."""
        try:
            # Validate and parse date
            try:
                event_date = datetime.strptime(date, "%Y-%m-%d")
            except ValueError:
                raise ToolSoftError(f"Invalid date format: {date}. Use YYYY-MM-DD")

            # Get Google Calendar service
            service = self._get_calendar_service()

            # Create event
            event = {
                "summary": "Appointment",
                "description": "Scheduled via Scheduler Tool",
                "start": {
                    "dateTime": event_date.isoformat() + "T10:00:00",
                    "timeZone": "UTC",
                },
                "end": {
                    "dateTime": (event_date + timedelta(hours=1)).isoformat() + "T11:00:00",
                    "timeZone": "UTC",
                },
            }

            # Insert event into primary calendar
            created_event = service.events().insert(calendarId="primary", body=event).execute()

            return f"Scheduled appointment on {date} at 10:00 UTC. Event ID: {created_event['id']}"

        except HttpError as error:
            raise ToolSoftError(f"Failed to schedule event: {error}")
        except Exception as e:
            raise ToolHardError(f"An unexpected error occurred: {str(e)}")