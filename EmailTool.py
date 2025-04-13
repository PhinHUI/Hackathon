import os
import base64
from email.mime.text import MIMEText
from pydantic import BaseModel, Field
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from portia.errors import ToolHardError, ToolSoftError
from portia.tool import Tool, ToolRunContext

class EmailToolSchema(BaseModel):
    email: str = Field(..., description="The email address of the patient")

class EmailTool(Tool[str]):
    id: str = "email_tool"
    name: str = "Email Tool"
    description: str = "Email patients for their appointments"
    args_schema: type[BaseModel] = EmailToolSchema
    output_schema: tuple[str, str] = ("str", "String output of the email sent")

    def _get_gmail_service(self):
        """Set up and return Google Gmail API service."""
        SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
        creds = None
        # Check for existing credentials
        if os.path.exists("token_email.json"):
            creds = Credentials.from_authorized_user_file("token_email.json", SCOPES)
        # If no valid credentials, prompt user to log in
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                if not os.path.exists("credentials.json"):
                    raise ToolHardError("credentials.json is required for Google Gmail API")
                flow = InstalledAppFlow.from_client_secrets_file("credentials.json", SCOPES)
                creds = flow.run_local_server(port=0)
                # Save credentials for future runs
                with open("token_email.json", "w") as token:
                    token.write(creds.to_json())
        return build("gmail", "v1", credentials=creds)

    def run(self, _: ToolRunContext, email: str) -> str:
        """Run the Email Tool to send an appointment reminder."""
        try:
            # Get Gmail service
            service = self._get_gmail_service()

            # Create email message
            message = MIMEText("Dear Patient,\n\nThis is a reminder for your upcoming appointment.\nDate: To be confirmed\nTime: To be confirmed\n\nBest regards,\nYour Clinic")
            message["to"] = email
            message["subject"] = "Your Appointment Reminder"

            # Encode message for Gmail API
            raw_message = base64.urlsafe_b64encode(message.as_bytes()).decode()
            message_body = {"raw": raw_message}

            # Send email
            sent_message = service.users().messages().send(userId="me", body=message_body).execute()

            return f"Email sent to {email}. Message ID: {sent_message['id']}"

        except HttpError as error:
            raise ToolSoftError(f"Failed to send email: {error}")
        except Exception as e:
            raise ToolHardError(f"An unexpected error occurred: {str(e)}")