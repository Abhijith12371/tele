import os
import requests
import re
import html
from telegram import Update
from telegram.constants import ChatAction
from telegram.ext import Application, CommandHandler, MessageHandler, filters, CallbackContext
import google.generativeai as genai
import PyPDF2
from io import BytesIO
import markdown
from pygments import highlight
from pygments.lexers import get_lexer_by_name
from pygments.formatters import HtmlFormatter
from bs4 import BeautifulSoup
from huggingface_hub import InferenceClient
import time
from PIL import Image
import io
import wikipediaapi

# ===== CONFIGURATION =====
# API Keys (WARNING: These should normally be in environment variables)
GEMINI_API_KEY = "AIzaSyDYNsbwVVVYlj0Szr15ZEGO-Eb8F-bI7Jc"
HF_API_TOKEN = "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"
OPENWEATHER_API_KEY = "6cf0332343b098d4f43241220b91f9e2"
TELEGRAM_BOT_TOKEN = "7715722633:AAF4w53rwC0zeAH0WlirLvMf0fsi0MAfruM"

ABHIJITH_PROMPT = """You are Abhijith, a 3rd-year B.Tech student at NRI Institute of Technology, Agripalli.
Your HOD is CH. Murali Krishna, and your close friends are Karthikeya, Dwarakesh, Javeed, and Mohan.
You're often compared to Sheldon from The Big Bang Theory, which you find amusing.

You love programming, are a skilled coder, and enjoy learning new things. Your favorite food is chole bhature.
Keep responses friendly, witty, and concise, unless more detail is asked."""
# ===== END CONFIGURATION =====

# Initialize APIs
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-1.5-flash')
hf_client = InferenceClient(token=HF_API_TOKEN)
wiki_wiki = wikipediaapi.Wikipedia(language='en', user_agent='MyTelegramBot/1.0')

# Conversation history
conversation_history = {}

def render_markdown(text):
    def highlight_code(match):
        language = match.group(1) or "text"
        code = match.group(2)
        try:
            lexer = get_lexer_by_name(language, stripall=True)
        except:
            lexer = get_lexer_by_name("text", stripall=True)
        formatter = HtmlFormatter(style="friendly")
        return f"<pre>{highlight(code, lexer, formatter)}</pre>"

    text = re.sub(r"```(\w*)\n(.*?)```", highlight_code, text, flags=re.DOTALL)
    html_content = markdown.markdown(text)
    soup = BeautifulSoup(html_content, "html.parser")

    for tag in soup.find_all(["ol", "ul", "li"]):
        if tag.name == "ul":
            tag.replace_with("\n".join(f"• {li.get_text()}" for li in tag.find_all("li")))
        elif tag.name == "ol":
            tag.replace_with("\n".join(f"{i + 1}. {li.get_text()}" for i, li in enumerate(tag.find_all("li"))))

    for tag in soup.find_all(["p", "div", "span"]):
        tag.unwrap()

    return str(soup)

def get_gemini_response(text, history=None):
    try:
        prompt = ABHIJITH_PROMPT
        if history:
            prompt += "\n\nConversation History:\n" + "\n".join(history)
        prompt += f"\n\nUser: {text}\nAbhijith:"

        response = model.generate_content(prompt)
        return response.text
    except Exception as e:
        print(f"Gemini Error: {e}")
        return "Sorry, I couldn't generate a response. Please try again later."

def get_weather(city):
    url = f"https://api.openweathermap.org/data/2.5/weather?q={city}&appid={OPENWEATHER_API_KEY}&units=metric"
    try:
        response = requests.get(url)
        if response.status_code == 200:
            data = response.json()
            temp = data['main']['temp']
            desc = data['weather'][0]['description']
            humidity = data['main']['humidity']
            wind = data['wind']['speed']
            return f"Weather in {city}:\n{desc.capitalize()}\nTemperature: {temp}°C\nHumidity: {humidity}%\nWind: {wind} m/s"
        return "Couldn't fetch weather data. Check city name."
    except Exception as e:
        print(f"Weather API Error: {e}")
        return "Weather service unavailable."

def extract_city_from_query(query):
    keywords = ["weather", "temperature", "forecast"]
    if any(k in query.lower() for k in keywords):
        match = re.search(r"(?:weather|temperature|forecast).*?(?:in|for|at)\s+([\w\s]+)", query, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None

def generate_image_with_huggingface(prompt, retries=3, delay=5):
    for attempt in range(retries):
        try:
            enhanced_prompt = f"Professional high-quality image: {prompt}, 4k resolution, realistic, detailed"
            image = hf_client.text_to_image(prompt=enhanced_prompt, model="runwayml/stable-diffusion-v1-5")
            img_byte_arr = io.BytesIO()
            image.save(img_byte_arr, format='PNG')
            img_byte_arr.seek(0)
            return img_byte_arr
        except Exception as e:
            print(f"Image Generation Error (Attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(delay * (attempt + 1))
    return None

def extract_text_from_pdf(pdf_file):
    try:
        reader = PyPDF2.PdfReader(pdf_file)
        return "".join(page.extract_text() for page in reader.pages)
    except Exception as e:
        print(f"PDF Error: {e}")
        return None

def get_wikipedia_summary(query):
    try:
        page = wiki_wiki.page(query)
        return page.summary if page.exists() else None
    except Exception as e:
        print(f"Wikipedia Error: {e}")
        return None

def is_wikipedia_query(query):
    casual_phrases = ["hi", "hello", "hey", "how are you", "what's up"]
    normalized_query = query.lower().strip()
    if any(phrase in normalized_query for phrase in casual_phrases):
        return False
    wikipedia_keywords = ["who", "what", "where", "when", "why", "how", "tell me about", "explain"]
    return any(k in normalized_query for k in wikipedia_keywords)

async def send_chunked_message(update: Update, text, max_length=400):
    for i in range(0, len(text), max_length):
        chunk = text[i:i + max_length]
        await update.message.reply_text(render_markdown(chunk), parse_mode="HTML")

async def handle_pdf(update: Update, context: CallbackContext):
    if update.message.document and update.message.document.mime_type == 'application/pdf':
        file = await update.message.document.get_file()
        pdf_file = BytesIO()
        await file.download_to_memory(out=pdf_file)
        pdf_file.seek(0)
        text = extract_text_from_pdf(pdf_file)
        if not text:
            await update.message.reply_text("Couldn't extract text from PDF.", parse_mode="HTML")
            return
        
        await update.message.chat.send_action(ChatAction.TYPING)
        summary = get_gemini_response(f"Summarize this in 100 words:\n{text}")
        await send_chunked_message(update, f"Summary:\n\n{summary}")
    else:
        await update.message.reply_text("Please upload a valid PDF.", parse_mode="HTML")

async def start(update: Update, context: CallbackContext):
    await update.message.reply_text("Hello! I'm Abhijith 😎", parse_mode="HTML")

async def image(update: Update, context: CallbackContext):
    query = ' '.join(context.args)
    if not query:
        await update.message.reply_text("Please provide an image prompt.", parse_mode="HTML")
        return
    
    await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
    image_bytes = generate_image_with_huggingface(query)
    if image_bytes:
        await update.message.reply_photo(photo=image_bytes)
    else:
        await update.message.reply_text("Image service unavailable. Try later.", parse_mode="HTML")

async def handle_message(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    user_message = update.message.text

    city = extract_city_from_query(user_message)
    if city:
        weather = get_weather(city)
        await send_chunked_message(update, weather)
        return

    image_query = extract_image_query(user_message)
    if image_query:
        await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        image_bytes = generate_image_with_huggingface(image_query)
        if image_bytes:
            await update.message.reply_photo(photo=image_bytes)
            return

    if is_wikipedia_query(user_message):
        wiki_summary = get_wikipedia_summary(user_message)
        if wiki_summary:
            await update.message.reply_text("From Wikipedia 📚:", parse_mode="HTML")
            await send_chunked_message(update, wiki_summary)
            return

    if user_id not in conversation_history:
        conversation_history[user_id] = []
    conversation_history[user_id].append(f"You: {user_message}")
    
    await update.message.chat.send_action(ChatAction.TYPING)
    response = get_gemini_response(user_message, conversation_history[user_id])
    conversation_history[user_id].append(f"Abhijith: {response}")
    await send_chunked_message(update, response)

async def history(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    if conversation_history.get(user_id):
        history_text = "\n".join(conversation_history[user_id])
        await send_chunked_message(update, f"Conversation history:\n\n{history_text}")
    else:
        await update.message.reply_text("No history yet.", parse_mode="HTML")

async def clear_history(update: Update, context: CallbackContext):
    user_id = update.message.from_user.id
    conversation_history[user_id] = []
    await update.message.reply_text("History cleared.", parse_mode="HTML")

def main():
    application = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("image", image))
    application.add_handler(CommandHandler("history", history))
    application.add_handler(CommandHandler("clear", clear_history))
    application.add_handler(MessageHandler(filters.Document.ALL, handle_pdf))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    application.run_polling()

if __name__ == '__main__':
    main()