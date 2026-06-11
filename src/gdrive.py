import os
import json
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

def upload_to_drive(dataset: list[dict]):
    """Authenticates using an in-memory service account token string and pushes the dataset payload."""
    folder_id = os.getenv("GDRIVE_FOLDER_ID")
    sa_json_str = os.getenv("GDRIVE_SERVICE_ACCOUNT_JSON")
    
    if not sa_json_str or not folder_id:
        print("[Warning] Google Drive operational variables missing. Dumping fallback file locally.")
        with open("fallback_output.json", "w") as f:
            json.dump(dataset, f, indent=4)
        return

    date_str = datetime.now().strftime("%Y-%m-%d")
    target_filename = f"fashion_analytics_{date_str}.json"
    
    with open(target_filename, "w") as tmp:
        json.dump(dataset, tmp, indent=4)

    try:
        sa_info = json.loads(sa_json_str)
        creds = service_account.Credentials.from_service_account_info(
            sa_info, 
            scopes=["https://www.googleapis.com/auth/drive.file"]
        )
        service = build("drive", "v3", credentials=creds)
        
        metadata = {
            "name": target_filename,
            "parents": [folder_id]
        }
        media = MediaFileUpload(target_filename, mimetype="application/json")
        
        uploaded_file = service.files().create(
            body=metadata,
            media_body=media,
            fields="id"
        ).execute()
        print(f"[Success] Batch uploaded. Node ID assigned: {uploaded_file.get('id')}")
        
    except Exception as e:
        print(f"[Error] Storage pipeline upload interrupted: {e}")
        
    finally:
        if os.path.exists(target_filename):
            os.remove(target_filename)
