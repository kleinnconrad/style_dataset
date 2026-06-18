from dotenv import load_dotenv
import os
from google import genai

load_dotenv()
api_key = os.getenv('GEMINI_API_KEY')
if not api_key:
    print("No API key found in .env")
else:
    client = genai.Client(api_key=api_key)
    try:
        models = client.models.list()
        for m in models:
            print(m.name)
    except Exception as e:
        print(f"Error list: {e}")
        try:
            import google.generativeai as gai
            gai.configure(api_key=api_key)
            for m in gai.list_models():
                print(m.name)
        except Exception as e2:
            print(f"Error generativeai: {e2}")
