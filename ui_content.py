"""
UnlimitedMC — данные интерфейса: локализация, приветствия, новости.

Перенос словарей из макета v6. Тексты RU/EN лежат рядом; выбор языка —
в основном модуле через L(ru, en).
"""
from __future__ import annotations

APP_VERSION = "0.5"
DESIGN_VERSION = "v6"

# короткие строки, которые часто перерисовываются
T = {
    "ru": {
        "install": "Установить", "installed": "Установлено ✓", "remove": "Удалить",
        "play": "Играть", "launching": "Запуск…",
        "mods": "модов", "noInstShort": "Сборок пока нет",
        "noInst": "У вас пока нет сборок", "createFirst": "Создать первую сборку",
        "empty": "Здесь пока пусто", "all": "Всё →",
    },
    "en": {
        "install": "Install", "installed": "Installed ✓", "remove": "Remove",
        "play": "Play", "launching": "Launching…",
        "mods": "mods", "noInstShort": "No instances yet",
        "noInst": "You have no instances yet", "createFirst": "Create your first instance",
        "empty": "Nothing here yet", "all": "All →",
    },
}

GREET = {
    "ru": ["Добро пожаловать в UnlimitedMC, {n}!","С возвращением, {n}!", "Рад тебя видеть, {n}", "Погнали, {n}!",
           "Привет, {n} 👋", "{n}, готов к приключениям?", "Давно не виделись, {n}!", "Тсшсйз, хю е тцфхцб тсхугхло еузпв, {n}!", "31062026"],
    "en": ["Welcome back, {n}!", "Good to see you, {n}", "Let’s go, {n}!",
           "Hey {n} 👋", "Ready for an adventure, {n}?", "Long time no see, {n}!", "I know you wanna play, {n}.", "31062026"],
}

# новости (group: mc | umc)
NEWS = [
    {"id": "garden", "group": "mc", "cover": "🌳",
     "tag": "Обновление", "tagEn": "Update", "date": "12 июня", "dateEn": "June 12",
     "title": "The Garden Awakens", "titleEn": "The Garden Awakens",
     "sum": "Новая листва, мобы и блоки уже появились в свежем снапшоте.",
     "sumEn": "New foliage, mobs and blocks arrived in the latest snapshot.",
     "body": ["В свежем снапшоте появилась пышная растительность, новые мобы и декоративные блоки — мир стал заметно живее.",
              "Часть нововведений войдёт в ближайшее крупное обновление, а пока их можно потестировать в экспериментальном режиме."],
     "bodyEn": ["The latest snapshot adds lush vegetation, new mobs and decorative blocks — the world feels much more alive.",
                "Some of these features will land in the next major update; for now you can try them in experimental mode."]},
    {"id": "inv", "group": "mc", "cover": "📦",
     "tag": "Снапшот", "tagEn": "Snapshot", "date": "5 июня", "dateEn": "June 5",
     "title": "Эксперименты с инвентарём", "titleEn": "Inventory experiments",
     "sum": "Разработчики тестируют новые рецепты и сортировку предметов.",
     "sumEn": "Devs are testing new recipes and item sorting.",
     "body": ["Команда экспериментирует с автоматической сортировкой предметов и обновлёнными рецептами крафта.",
              "Цель — сделать управление инвентарём удобнее, особенно в больших сборках с сотнями предметов."],
     "bodyEn": ["The team is experimenting with automatic item sorting and updated crafting recipes.",
                "The goal is easier inventory management, especially in large modpacks with hundreds of items."]},
    {"id": "live", "group": "mc", "cover": "🎉",
     "tag": "Событие", "tagEn": "Event", "date": "1 июня", "dateEn": "June 1",
     "title": "Minecraft Live", "titleEn": "Minecraft Live",
     "sum": "Голосование за нового моба и анонсы будущих обновлений.",
     "sumEn": "Vote for the new mob and see upcoming announcements.",
     "body": ["Скоро состоится Minecraft Live — традиционное событие с анонсами и голосованием за нового моба.",
              "Сообщество снова выберет, какое существо добавят в игру следующим. Готовь свой голос!"],
     "bodyEn": ["Minecraft Live is coming soon — the traditional event with announcements and a vote for a new mob.",
                "The community will choose which creature gets added next. Get your vote ready!"]},
    {"id": "content", "group": "umc", "cover": "✨",
     "tag": "v0.3", "tagEn": "v0.3", "date": "10 июня", "dateEn": "June 10",
     "title": "Modrinth и CurseForge", "titleEn": "Modrinth and CurseForge",
     "sum": "Моды, шейдеры и сборки теперь ставятся прямо из лаунчера.",
     "sumEn": "Mods, shaders and packs now install right from the launcher.",
     "body": ["Теперь моды, шейдеры, ресурспаки и сборки можно искать и устанавливать прямо в UnlimitedMC — в один клик, в нужную сборку.",
              "Поддерживаются оба крупнейших каталога: Modrinth и CurseForge. Больше не нужно качать файлы вручную и раскидывать их по папкам."],
     "bodyEn": ["You can now search and install mods, shaders, resource packs and modpacks right inside UnlimitedMC — one click into the instance you choose.",
                "Both major catalogs are supported: Modrinth and CurseForge. No more downloading files by hand and sorting them into folders."]},
    {"id": "profiles", "group": "umc", "cover": "🎨",
     "tag": "Кастомизация", "tagEn": "Customization", "date": "7 июня", "dateEn": "June 7",
     "title": "Профили и друзья", "titleEn": "Profiles and friends",
     "sum": "Профиль, друзья и статусы — скоро, когда подключим сервер.",
     "sumEn": "Profiles, friends and statuses — coming once the server is online.",
     "body": ["В планах — профили игроков: имя, @username, аватар и шапка, описание, любимые моды и ссылки на соцсети.",
              "А ещё друзья и статусы. Раздел появится позже, вместе с серверной частью — пока вход недоступен."],
     "bodyEn": ["Planned: player profiles — name, @username, avatar and header, bio, favorite mods and social links.",
                "Plus friends and statuses. This will arrive later with the server side — sign-in is not available yet."]},
    {"id": "speed", "group": "umc", "cover": "⚡",
     "tag": "Скорость", "tagEn": "Speed", "date": "3 июня", "dateEn": "June 3",
     "title": "Запуск стал быстрее", "titleEn": "Faster launch",
     "sum": "Сборки грузятся заметно шустрее, чем раньше.",
     "sumEn": "Instances load noticeably faster than before.",
     "body": ["Мы оптимизировали загрузку сборок — теперь игра запускается заметно быстрее, особенно на больших модпаках.",
              "Дальше в планах — ускорить и саму установку модов."],
     "bodyEn": ["We optimized instance loading — the game now starts noticeably faster, especially on large modpacks.",
                "Next up: speeding up mod installation itself."]},
]

TYPES = [("Моды", "Mods", "mod"), ("Сборки", "Modpacks", "modpack"),
         ("Ресурспаки", "Resource Packs", "resourcepack"), ("Шейдеры", "Shaders", "shader")]
LOADERS = ["vanilla", "fabric", "forge", "quilt"]
COMMON_VERSIONS = ["1.21.4", "1.21.1", "1.20.6", "1.20.1", "1.19.2", "1.18.2", "1.16.5", "1.12.2"]

LOADER_EMOJI = {"fabric": "🧵", "forge": "⚙️", "neoforge": "🔥", "quilt": "🧶", "vanilla": "🟩"}
