import discord
from discord import app_commands
from discord.player import FFmpegPCMAudio
import requests
import json
import asyncio
import io
import tempfile
from config import TOKEN
import re
import os

server_statuses = {}

activity = discord.Activity(name="起動中…", type=discord.ActivityType.playing)
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
client = discord.Client(intents=intents, activity=activity)
tree = app_commands.CommandTree(client)
voice_clients = {}
text_channels = {}
current_speaker = {}  # ギルドごとに話者を設定するための辞書

FFMPEG_PATH = "C:/ffmpeg/bin/ffmpeg.exe"

class ServerStatus:
    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        asyncio.create_task(self.save_task())
    
    async def save_task(self):
        while True:
            # guild.idを保存するロジックをここに追加
            print(f"Saving guild id: {self.guild_id}")
            await asyncio.sleep(60)  # 60秒ごとに保存

class AivisAdapter:
    def __init__(self):
        # APIサーバーのエンドポイントURL
        self.URL = "http://127.0.0.1:10101"
        # 話者ID (話させたい音声モデルidに変更してください)
        self.speaker = {}

    def speak_voice(self, text: str, voice_client: discord.VoiceClient):
        params = {"text": text, "speaker": self.speaker}
        query_response = requests.post(f"{self.URL}/audio_query", params=params).json()

        audio_response = requests.post(
            f"{self.URL}/synthesis",
            params={"speaker": self.speaker},
            data=json.dumps(query_response)
        )
        voice_client.play(create_ffmpeg_audio_source(io.BytesIO(audio_response.content)))

def create_ffmpeg_audio_source(path: str):
    return FFmpegPCMAudio(path, executable=FFMPEG_PATH)

def post_audio_query(text: str, speaker: int):
    params = {"text": text, "speaker": speaker}
    response = requests.post("http://127.0.0.1:10101/audio_query", params=params)
    return response.json()

def post_synthesis(audio_query: dict, speaker: int):
    response = requests.post(
        "http://127.0.0.1:10101/synthesis",
        params={"speaker": speaker},
        headers={"accept": "audio/wav", "Content-Type": "application/json"},
        data=json.dumps(audio_query),
    )
    return response.content

voice_settings = {
    "volume": {},  # デフォルトの音量を0.2に設定
    "pitch": {},
    "rate": {},
    "speed": {},
    "style_strength": {},
    "tempo": {}
}

def adjust_audio_query(audio_query: dict, guild_id: int):
    audio_query["volumeScale"] = voice_settings["volume"].get(guild_id, 0.2)  # デフォルトの音量を0.2に設定
    audio_query["pitchScale"] = voice_settings["pitch"].get(guild_id, 0.0)
    audio_query["rateScale"] = voice_settings["rate"].get(guild_id, 1.0)
    audio_query["speedScale"] = voice_settings["speed"].get(guild_id, 1.0)
    audio_query["styleStrength"] = voice_settings["style_strength"].get(guild_id, 1.0)
    audio_query["tempoScale"] = voice_settings["tempo"].get(guild_id, 1.0)
    return audio_query

DICTIONARY_FILE = "guild_dictionaries.json"

def load_dictionaries():
    global guild_dictionaries
    if os.path.exists(DICTIONARY_FILE):
        with open(DICTIONARY_FILE, 'r', encoding='utf-8') as f:
            guild_dictionaries = json.load(f)
    else:
        guild_dictionaries = {}

def save_dictionaries():
    with open(DICTIONARY_FILE, 'w', encoding='utf-8') as f:
        json.dump(guild_dictionaries, f, ensure_ascii=False, indent=4)

guild_dictionaries = {}
load_dictionaries()

def add_to_dictionary(guild_id: int, word: str, pronunciation: str):
    if guild_id not in guild_dictionaries:
        guild_dictionaries[guild_id] = {}
    guild_dictionaries[guild_id][word] = pronunciation
    save_dictionaries()
    # 辞書をAPIサーバーに登録
    requests.post("http://localhost:10101/user_dict_word", json={"surface": word, "pronunciation": pronunciation})

def edit_dictionary(guild_id: int, word: str, new_pronunciation: str):
    if guild_id in guild_dictionaries and word in guild_dictionaries[guild_id]:
        guild_dictionaries[guild_id][word] = new_pronunciation
        save_dictionaries()
        # 辞書をAPIサーバーに登録
        requests.post("http://localhost:10101/user_dict_word", json={"surface": word, "pronunciation": new_pronunciation})

def remove_from_dictionary(guild_id: int, word: str):
    if guild_id in guild_dictionaries and word in guild_dictionaries[guild_id]:
        del guild_dictionaries[guild_id][word]
        save_dictionaries()
        # 辞書をAPIサーバーから削除
        requests.delete("http://localhost:10101/user_dict_word", json={"surface": word})

def speak_voice(text: str, speaker: int, guild_id: int):
    audio_query = post_audio_query(text, speaker)
    audio_query = adjust_audio_query(audio_query, guild_id)
    audio_content = post_synthesis(audio_query, speaker)
    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio_file:
        temp_audio_file.write(audio_content)
        temp_audio_file_path = temp_audio_file.name
    return temp_audio_file_path

@client.event
async def on_ready():
    print("起動完了")
    try:
        synced = await tree.sync()
        print(f"{len(synced)}個のコマンドを同期しました")
    except Exception as e:
        print(e)

    # 15秒毎にアクティヴィティを更新します
    while True:
        joinserver = len(client.guilds)
        servers = str(joinserver)
        await client.change_presence(
            activity=discord.CustomActivity(name="サーバー数:" + servers))
        await asyncio.sleep(15)
        joinvc = len(client.voice_clients)
        vc = str(joinvc)
        await client.change_presence(
            activity=discord.CustomActivity(name="VC:" + vc))
        await asyncio.sleep(15)

@tree.command(
    name="join", description="ボイスチャンネルに接続します。"
)
@app_commands.describe(
    voice_channel="接続するボイスチャンネルを選択してください。"
)
async def join_command(interaction: discord.Interaction, voice_channel: discord.VoiceChannel = None):
    global voice_clients, text_channels
    if interaction.guild is None:
        await interaction.response.send_message("このコマンドはサーバー内でのみ使用できます。", ephemeral=True)
        return
    if voice_channel is None:
        if interaction.user.voice is None or interaction.user.voice.channel is None:
            await interaction.response.send_message("あなたはボイスチャンネルに接続していません。", ephemeral=True)
            return
        voice_channel = interaction.user.voice.channel
    text_channels[interaction.guild.id] = interaction.channel
    try:
        if interaction.guild.id in voice_clients and voice_clients[interaction.guild.id].is_connected():
            await voice_clients[interaction.guild.id].move_to(voice_channel)
            await interaction.response.send_message(f"{voice_channel.name} に移動しました。")
        else:
            global server_statuses
            voice_clients[interaction.guild.id] = await voice_channel.connect()
            await interaction.response.send_message(f"{voice_channel.name} に接続しました。")
            server_statuses[interaction.guild.id] = ServerStatus(interaction.guild.id)
        
        # 接続完了時に音声を鳴らす
        path = speak_voice("接続しました。", current_speaker.get(interaction.guild.id, 888753760), interaction.guild.id)
        while voice_clients[interaction.guild.id].is_playing():
            await asyncio.sleep(1)
        voice_clients[interaction.guild.id].play(create_ffmpeg_audio_source(path))
    except discord.errors.ClientException as e:
        await interaction.response.send_message(f"エラーが発生しました: {str(e)}", ephemeral=True)

@tree.command(
    name="leave", description="ボイスチャンネルから切断します。"
)
async def leave_command(interaction: discord.Interaction):
    global voice_clients
    if interaction.guild.id in voice_clients:
        await voice_clients[interaction.guild.id].disconnect()
        del voice_clients[interaction.guild.id]
    await interaction.response.send_message("切断しました。")

# ping応答コマンドを定義します
@tree.command(
    name="ping", description="BOTの応答時間をテストします。"
)
async def ping_command(interaction: discord.Interaction):
    text = f"Pong! BotのPing値は{round(client.latency*1000)}msです。"
    embed = discord.Embed(title="Latency", description=text)
    print(text)
    await interaction.response.send_message(embed=embed)

@client.event
async def on_voice_state_update(member, before, after):
    global voice_clients, current_speaker
    if member.guild.id in voice_clients and voice_clients[member.guild.id].is_connected():
        if before.channel is None and after.channel is not None:
            # ユーザーがボイスチャンネルに参加したとき
            if voice_clients[member.guild.id].channel == after.channel:
                nickname = member.display_name
                path = speak_voice(f"{nickname} さんが入室しました。", current_speaker.get(member.guild.id, 888753760), member.guild.id)
                while voice_clients[member.guild.id].is_playing():
                    await asyncio.sleep(1)
                voice_clients[member.guild.id].play(create_ffmpeg_audio_source(path))
        elif before.channel is not None and after.channel is None:
            # ユーザーがボイスチャンネルから退出したとき
            if voice_clients[member.guild.id].channel == before.channel:
                nickname = member.display_name
                path = speak_voice(f"{nickname} さんが退室しました。", current_speaker.get(member.guild.id, 888753760), member.guild.id)
                while voice_clients[member.guild.id].is_playing():
                    await asyncio.sleep(1)
                voice_clients[member.guild.id].play(create_ffmpeg_audio_source(path))
                
                # ボイスチャンネルに誰もいなくなったら退室
                if len(voice_clients[member.guild.id].channel.members) == 1:  # ボイスチャンネルにいるのがBOTだけの場合
                    await voice_clients[member.guild.id].disconnect()
                    del voice_clients[member.guild.id]

# URL、ファイル、EMBEDを除外するための正規表現パターン
URL_PATTERN = r"https?://[^\s]+"

@client.event
async def on_message(message):
    if message.author.bot:
        print("Message is from a bot, ignoring.")
        return
    if message.embeds:
        print("Message contains embeds, ignoring.")
        return
    if message.attachments:
        print("Message contains attachments, ignoring.")
        return
    if any(str(emoji) in message.content for emoji in message.guild.emojis):
        print("Message contains emojis, ignoring.")
        return
    if re.search(URL_PATTERN, message.content):
        print("Message contains a URL, ignoring.")
        return
    global voice_clients, text_channels, current_speaker
    voice_client = voice_clients.get(message.guild.id)
    if voice_client and voice_client.is_connected() and message.channel == text_channels.get(message.guild.id):
        print("Voice client is connected and message is in the correct channel, handling message.")
        asyncio.create_task(handle_message(message, voice_client))
    else:
        print("Voice client is not connected or message is in the wrong channel, ignoring message.")

async def handle_message(message, voice_client):
    print(f"Handling message: {message.content}")
    speaker_id = current_speaker.get(message.guild.id, 888753760)  # デフォルトの話者ID
    path = speak_voice(message.content, speaker_id, message.guild.id)
    while voice_client.is_playing():
        await asyncio.sleep(0.1)
    voice_client.play(create_ffmpeg_audio_source(path))
    print(f"Finished playing message: {message.content}")
        

import json

# スピーカー情報を読み込む
with open('speakers.json', 'r', encoding='utf-8') as f:
    speakers = json.load(f)

# スピーカー名とスタイルのリストを作成
speaker_choices = [
    app_commands.Choice(name=f"{speaker.get('name')} - {style.get('name')}", value=str(style.get('id')))
    for speaker in speakers
    for style in speaker.get('styles', [])
]

def get_speaker_info_by_id(style_id: int):
    for speaker in speakers:
        for style in speaker.get('styles', []):
            if style.get('id') == style_id:
                return speaker, style
    return None, None

@tree.command(
    name="set_speaker", description="話者を切り替えます。"
)
@app_commands.describe(
    speaker_choice="設定する話者とスタイルを選択してください。"
)
@app_commands.choices(speaker_choice=speaker_choices)
async def set_speaker_command(interaction: discord.Interaction, speaker_choice: str):
    print(f"Received speaker_choice: {speaker_choice}")  # デバッグ用にspeaker_choiceを出力
    global current_speaker
    speaker_info, style_info = get_speaker_info_by_id(int(speaker_choice))
    if speaker_info and style_info:
        current_speaker[interaction.guild.id] = int(speaker_choice)
        await interaction.response.send_message(f"話者を {speaker_info['name']} のスタイル {style_info['name']} に切り替えました。")
    else:
        await interaction.response.send_message("無効な選択です。", ephemeral=True)

@tree.command(
    name="set_volume", description="音量を設定します。"
)
@app_commands.describe(
    volume="設定する音量を入力してください (0.0 - 2.0)。"
)
async def set_volume_command(interaction: discord.Interaction, volume: float):
    if 0.0 <= volume <= 2.0:
        voice_settings["volume"][interaction.guild.id] = volume
        await interaction.response.send_message(f"音量を {volume} に設定しました。")
    else:
        await interaction.response.send_message("無効な音量値です。0.0から2.0の間で設定してください。", ephemeral=True)

@tree.command(
    name="set_pitch", description="音高を設定します。"
)
@app_commands.describe(
    pitch="設定する音高を入力してください (-1.0 - 1.0)。"
)
async def set_pitch_command(interaction: discord.Interaction, pitch: float):
    if -1.0 <= pitch <= 1.0:
        voice_settings["pitch"][interaction.guild.id] = pitch
        await interaction.response.send_message(f"音高を {pitch} に設定しました。")
    else:
        await interaction.response.send_message("無効な音高値です。-1.0から1.0の間で設定してください。", ephemeral=True)

@tree.command(
    name="set_speed", description="話速を設定します。"
)
@app_commands.describe(
    speed="設定する話速を入力してください (0.5 - 2.0)。"
)
async def set_speed_command(interaction: discord.Interaction, speed: float):
    if 0.5 <= speed <= 2.0:
        voice_settings["speed"][interaction.guild.id] = speed
        await interaction.response.send_message(f"話速を {speed} に設定しました。")
    else:
        await interaction.response.send_message("無効な話速値です。0.5から2.0の間で設定してください。", ephemeral=True)

@tree.command(
    name="set_style_strength", description="スタイルの強さを設定します。"
)
@app_commands.describe(
    style_strength="設定するスタイルの強さを入力してください (0.0 - 2.0)。"
)
async def set_style_strength_command(interaction: discord.Interaction, style_strength: float):
    if 0.0 <= style_strength <= 2.0:
        voice_settings["style_strength"][interaction.guild.id] = style_strength
        await interaction.response.send_message(f"スタイルの強さを {style_strength} に設定しました。")
    else:
        await interaction.response.send_message("無効なスタイルの強さです。0.0から2.0の間で設定してください。", ephemeral=True)

@tree.command(
    name="set_tempo", description="テンポの緩急を設定します。"
)
@app_commands.describe(
    tempo="設定するテンポの緩急を入力してください (0.5 - 2.0)。"
)
async def set_tempo_command(interaction: discord.Interaction, tempo: float):
    if 0.5 <= tempo <= 2.0:
        voice_settings["tempo"][interaction.guild.id] = tempo
        await interaction.response.send_message(f"テンポの緩急を {tempo} に設定しました。")
    else:
        await interaction.response.send_message("無効なテンポの緩急です。0.5から2.0の間で設定してください。", ephemeral=True)

@tree.command(
    name="add_word", description="辞書に単語を登録します。"
)
@app_commands.describe(
    word="登録する単語を入力してください。",
    pronunciation="単語の発音をカタカナで入力してください。",
    accent_type="アクセントの種類を入力してください。"
)
async def add_word_command(interaction: discord.Interaction, word: str, pronunciation: str, accent_type: int):
    # 辞書をAPIサーバーに登録
    response = requests.post("http://127.0.0.1:10101/user_dict_word", json={"surface": word, "pronunciation": pronunciation, "accent_type": accent_type})
    if response.status_code == 200:
        await interaction.response.send_message(f"単語 '{word}' を発音 '{pronunciation}' で辞書に登録しました。")
    else:
        await interaction.response.send_message(f"単語 '{word}' の登録に失敗しました。", ephemeral=True)

def get_word_uuid(word: str):
    response = requests.get(f"http://127.0.0.1:10101/user_dict?surface={word}")
    if response.status_code == 200:
        word_data = response.json()
        if word_data:
            return word_data[0].get("uuid")
    return None

@tree.command(
    name="edit_word", description="辞書の単語を編集します。"
)
@app_commands.describe(
    word="編集する単語を入力してください。",
    new_pronunciation="新しい発音をカタカナで入力してください。",
    accent_type="アクセントの種類を入力してください。"
)
async def edit_word_command(interaction: discord.Interaction, word: str, new_pronunciation: str, accent_type: int):
    # 単語のUUIDを取得
    word_uuid = get_word_uuid(word)
    if word_uuid:
        # 辞書をAPIサーバーに登録
        response = requests.put(f"http://127.0.0.1:10101/user_dict_word/{word_uuid}", json={"surface": word, "pronunciation": new_pronunciation, "accent_type": accent_type})
        if response.status_code == 200:
            await interaction.response.send_message(f"単語 '{word}' の発音を '{new_pronunciation}' に編集しました。")
        else:
            await interaction.response.send_message(f"単語 '{word}' の編集に失敗しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"単語 '{word}' が見つかりませんでした。", ephemeral=True)

@tree.command(
    name="remove_word", description="辞書から単語を削除します。"
)
@app_commands.describe(
    word="削除する単語を入力してください。"
)
async def remove_word_command(interaction: discord.Interaction, word: str):
    # 単語のUUIDを取得
    word_uuid = get_word_uuid(word)
    if word_uuid:
        # 辞書をAPIサーバーから削除
        response = requests.delete(f"http://127.0.0.1:10101/user_dict_word/{word_uuid}")
        if response.status_code == 200:
            await interaction.response.send_message(f"単語 '{word}' を辞書から削除しました。")
        else:
            await interaction.response.send_message(f"単語 '{word}' の削除に失敗しました。", ephemeral=True)
    else:
        await interaction.response.send_message(f"単語 '{word}' が見つかりませんでした。", ephemeral=True)

print(f"TOKEN: {TOKEN}")  # デバッグ用にTOKENを出力
client.run(TOKEN)