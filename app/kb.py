"""База знаний: разделы из Markdown-файлов и полнотекстовый поиск BM25.

Каждый файл в data/kb — одна тема. Раздел начинается с заголовка «## Название [код]».
Строка <!-- вопросы: ... --> перечисляет типичные формулировки клиентов: они участвуют в поиске,
но не показываются в ответе. Владелец бизнеса правит эти файлы сам, без программиста.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

STOP_WORDS = frozenset(
    """
    и в во на с со по за к ко у о об от до из для не ни а но или ли же бы то это этот эта эти как что чтобы
    я мы вы ты он она они мне меня мой моя мою мое мои моего наш ваш ваша ваше ваши вас вам вами есть можно
    нужно надо хочу хотел хотела если здравствуйте добрый день вечер утро привет подскажите пожалуйста
    спасибо скажите еще уже там тут вот так очень просто
    """.split()
)
# Грубое приведение слов к общей основе: первые пять букв, затем словарь синонимов предметной области.
SYNONYMS = {
    "верну": "возвр", "верни": "возвр",
    "приве": "доста",
    "оплач": "оплат", "запла": "оплат", "плати": "оплат",
    "стоит": "стоим", "цена": "стоим", "цены": "стоим", "цену": "стоим", "ценам": "стоим", "платн": "стоим",
    "собер": "сборк", "собра": "сборк", "сборщ": "сборк", "собир": "сборк",
    "помен": "обмен",
    "денег": "деньг",
    "брако": "брак", "дефек": "брак", "слома": "брак",
    "отказ": "отмен",
    "суббо": "выход", "воскр": "выход",
}
_SECTION_RE = re.compile(r"^##\s+(?P<title>.+?)\s+\[(?P<code>[a-z0-9-]+)\]\s*$")
_ALIASES_RE = re.compile(r"<!--\s*вопросы:\s*(?P<aliases>.*?)\s*-->")


def tokens(text: str) -> list[str]:
    words = re.findall(r"[a-zа-я0-9]+", text.lower().replace("ё", "е"))
    stems = (word[:5] for word in words if word not in STOP_WORDS and len(word) > 1)
    return [SYNONYMS.get(stem, stem) for stem in stems]


@dataclass(frozen=True)
class Section:
    id: str  # «dostavka/sroki»
    doc: str
    doc_title: str
    title: str
    text: str
    aliases: tuple[str, ...]


def load_kb(directory: Path) -> list[Section]:
    sections: list[Section] = []
    for path in sorted(directory.glob("*.md")):
        doc_title = path.stem
        current: dict | None = None

        def flush() -> None:
            if current and current["lines"]:
                sections.append(
                    Section(
                        id=f"{path.stem}/{current['code']}",
                        doc=path.stem,
                        doc_title=doc_title,
                        title=current["title"],
                        text=" ".join(" ".join(current["lines"]).split()),
                        aliases=tuple(current["aliases"]),
                    )
                )

        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line.startswith("# "):
                doc_title = line[2:].strip()
                continue
            heading = _SECTION_RE.match(line)
            if heading:
                flush()
                current = {"title": heading.group("title"), "code": heading.group("code"), "aliases": [], "lines": []}
                continue
            if current is None or not line:
                continue
            aliases = _ALIASES_RE.match(line)
            if aliases:
                current["aliases"] += [alias.strip() for alias in aliases.group("aliases").split(";") if alias.strip()]
            else:
                current["lines"].append(line)
        flush()
    return sections


@dataclass(frozen=True)
class Hit:
    section: Section
    score: float
    coverage: float  # доля «веса» слов вопроса, найденных в разделе: от 0 до 1


class Index:
    """BM25 по разделам. Заголовок весит втрое, типичные вопросы — вдвое больше основного текста."""

    def __init__(self, sections: list[Section], k1: float = 1.4, b: float = 0.6):
        self.sections = sections
        self.k1, self.b = k1, b
        self.frequencies: list[Counter] = []
        for section in sections:
            counter: Counter = Counter()
            for token in tokens(section.title):
                counter[token] += 3
            for alias in section.aliases:
                for token in tokens(alias):
                    counter[token] += 2
            for token in tokens(section.text):
                counter[token] += 1
            self.frequencies.append(counter)
        self.lengths = [sum(counter.values()) for counter in self.frequencies]
        self.average_length = sum(self.lengths) / max(len(self.lengths), 1)
        document_frequency = Counter(token for counter in self.frequencies for token in counter)
        total = len(sections)
        self.idf = {
            token: math.log(1 + (total - count + 0.5) / (count + 0.5)) for token, count in document_frequency.items()
        }
        self.max_idf = max(self.idf.values(), default=1.0)

    def search(self, query: str, limit: int = 3) -> list[Hit]:
        query_tokens = list(dict.fromkeys(tokens(query)))
        if not query_tokens:
            return []
        # Слова, которых нет в базе, считаются самыми «весомыми»: вопрос про неизвестное не должен
        # получить уверенный ответ только потому, что в нём есть слово «заказ».
        total_weight = sum(self.idf.get(token, self.max_idf) for token in query_tokens)
        hits: list[Hit] = []
        for index, counter in enumerate(self.frequencies):
            score = matched = 0.0
            norm = self.k1 * (1 - self.b + self.b * self.lengths[index] / self.average_length)
            for token in query_tokens:
                frequency = counter.get(token, 0)
                if frequency:
                    score += self.idf[token] * frequency * (self.k1 + 1) / (frequency + norm)
                    matched += self.idf[token]
            if score > 0:
                hits.append(Hit(self.sections[index], score, round(matched / total_weight, 6)))
        hits.sort(key=lambda hit: -hit.score)
        return hits[:limit]
