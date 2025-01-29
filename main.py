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

server_statuses = {}

activity = discord.Activity(name="起動中…", type=discord.ActivityType.playing)
intents = discord.Intents.default()
intents.message_content = True
intents.voice_states = True
client = discord.Client(intents=intents, activity=activity)
tree = app_commands.CommandTree(client)
voice_clients = {}
text_channels = {}
current_speaker = 1196801504  # デフォルトの話者ID

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
        self.speaker = 1196801504

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

def speak_voice(text: str, speaker: int):
    audio_query = post_audio_query(text, speaker)
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
    voice_channel="接続するボイスチャンネルを選択してください。")
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
        path = speak_voice("接続しました。", current_speaker)
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
                path = speak_voice(f"{member.name} が入室しました。", current_speaker)
                while voice_clients[member.guild.id].is_playing():
                    await asyncio.sleep(1)
                voice_clients[member.guild.id].play(create_ffmpeg_audio_source(path))
        elif before.channel is not None and after.channel is None:
            # ユーザーがボイスチャンネルから退出したとき
            if voice_clients[member.guild.id].channel == before.channel:
                path = speak_voice(f"{member.name} が退室しました。", current_speaker)
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
    print(f"Received message: {message.content}")
    if message.author.bot:
        return
    if message.embeds:
        return
    if message.attachments:
        return
    if message.guild.emojis:
        return
    if re.search(URL_PATTERN, message.content):
        return
    else:
        if message.guild.id in voice_clients and voice_clients[message.guild.id].is_connected():
            global voice_clients, text_channels, current_speaker
            path = speak_voice(message.content, current_speaker)
            while voice_clients[message.guild.id].is_playing():
                await asyncio.sleep(0.1)
            voice_clients[message.guild.id].play(create_ffmpeg_audio_source(path))

print(f"TOKEN: {TOKEN}")  # デバッグ用にTOKENを出力
client.run(TOKEN)