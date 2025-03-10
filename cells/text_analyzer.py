import json
import requests
import yaml
import re
import os
from pkg.core import app
from collections import Counter
from typing import Tuple, List, Dict, Any
from plugins.waifu5.cells.config import ConfigManager


class TextAnalyzer:
    LOADED_DICTIONARIES = {}

    ap: app.Application

    def __init__(self, ap: app.Application):
        self.ap = ap

    async def _load_yaml_dict(self, file: str) -> Dict[str, list]:
        """
        Load yaml dictionary file.
        :param file: yaml file path
        """
        # 如果字典已经加载过，则直接返回
        if file in TextAnalyzer.LOADED_DICTIONARIES:
            return TextAnalyzer.LOADED_DICTIONARIES[file]

        config = ConfigManager(f"data/plugins/waifu5/config/{file}", f"plugins/waifu5/templates/{file}")
        await config.load_config(completion=False)

        # 将加载的字典数据存入全局变量
        TextAnalyzer.LOADED_DICTIONARIES[file] = config.data
        return config.data

    def _call_texsmart_api(self, text: str) -> Dict[str, Any]:
        url = "https://texsmart.qq.com/api"
        obj = {"str": text}
        req_str = json.dumps(obj).encode()

        try:
            r = requests.post(url, data=req_str)
            r.encoding = "utf-8"
            return r.json()
        except requests.RequestException as e:
            print(f"Request failed: {e}")
            return {"error": "Request failed"}
        except json.JSONDecodeError as e:
            print(f"JSON decode failed: {e}")
            return {"error": "JSON decode failed"}
        except Exception as e:
            print(f"An unexpected error occurred: {e}")
            return {"error": "An unexpected error occurred"}

    def _parse_texsmart_response(self, response):
        parsed_data = {"word_list": [], "phrase_list": [], "entity_list": []}

        for word in response.get("word_list", []):
            parsed_data["word_list"].append({"str": word["str"], "tag": word["tag"]})

        for phrase in response.get("phrase_list", []):
            parsed_data["phrase_list"].append({"str": phrase["str"], "tag": phrase["tag"]})

        for entity in response.get("entity_list", []):
            entity_meaning = entity.get("meaning", {})
            parsed_data["entity_list"].append({"str": entity["str"], "tag": entity["tag"], "i18n": entity["type"].get("i18n", ""), "related": entity_meaning.get("related", [])})

        return parsed_data

    async def term_freq(self, text: str) -> Tuple[Counter, List[str], List[str]]:
        """
        Calculate word count and retrieve i18n information.
        :param text: text string
        """
        text = await self._remove_meaningless(text)
        words = []
        i18n_list = []
        related_list = []

        response = self._call_texsmart_api(text)
        parsed_data = self._parse_texsmart_response(response)

        words = [w["str"] for w in parsed_data["word_list"]]  # 基础粒度分词
        for entity in parsed_data["entity_list"]:
            i18n_list.append(entity["i18n"])  # 实体类型标注，类型名字的自然语言表达（中文或者英文）
            related_list.extend(entity["related"])  # 实体的语义联想

        words = self._remove_punctuation(words)  # 删除标点符号项目
        words = self._remove_unless_words(words)  # 删除无意义标签
        i18n_list = self._remove_punctuation(i18n_list)
        related_list = self._remove_punctuation(related_list)

        words = sorted(set(words))
        i18n_list = sorted(set(i18n_list))
        related_list = sorted(set(related_list))

        term_freq_counter = Counter(words)
        return term_freq_counter, i18n_list, related_list

    async def sentiment(self, text: str) -> Dict[str, Any]:
        """
        Calculate the occurrences of each sentiment category words in text.
        :param text: text string
        """
        text = await self._remove_meaningless(text)
        result_dict = {"positive_num": 0, "negative_num": 0}

        positive_dict = await self._load_yaml_dict("positive")
        positive_list = positive_dict.get("positive", [])
        negative_dict = await self._load_yaml_dict("negative")
        negative_list = negative_dict.get("negative", [])

        response = self._call_texsmart_api(text)
        parsed_data = self._parse_texsmart_response(response)
        words = [w["str"] for w in parsed_data["phrase_list"]]

        # 移除分词中标点符号项目
        words = self._remove_punctuation(words)

        word_num = len(words)
        output = {"positive": [], "negative": [], "unrecognized": []}

        for word in words:
            if word in positive_list:
                result_dict["positive_num"] += 1
                output["positive"].append(word)
            elif any(neg_word in word for neg_word in negative_list):
                result_dict["negative_num"] += 1
                output["negative"].append(word)
            else:
                output["unrecognized"].append(word)

        output["unrecognized"] = sorted(set(output["unrecognized"]))
        for item in output.keys():
            print(f"{item}: {output[item]}")

        result_dict["word_num"] = word_num

        self._save_unrecognized_words(output["unrecognized"])

        return result_dict

    def _remove_punctuation(self, words: List[str]) -> List[str]:
        """
        Remove all punctuation from the list of words.
        :param words: list of words
        :return: list of words without punctuation
        """
        punct_pattern = re.compile(r"[^\w]", re.UNICODE)
        return [word for word in words if not punct_pattern.search(word)]

    def _save_unrecognized_words(self, words: List[str]):
        """
        Save unrecognized words to a YAML file after sorting and removing duplicates.
        :param words: List of unrecognized words
        :param filename: The name of the file to save the words in
        """
        existing_words = []
        dict_file_path = f"data/plugins/waifu5/config/unrecognized_words.yaml"

        if os.path.exists(dict_file_path):
            with open(dict_file_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f)
                if data and "unrecognized" in data:
                    existing_words = data["unrecognized"]

        # Combine existing words with new words, remove duplicates, and sort
        combined_words = sorted(set(existing_words + words))

        with open(dict_file_path, "w", encoding="utf-8") as f:
            yaml.dump({"unrecognized": combined_words}, f, allow_unicode=True)

    async def _remove_meaningless(self, text: str) -> str:
        """
        Remove meaningless words and punctuation from the text.
        :param text: input text
        """
        meaningless_dict = await self._load_yaml_dict("meaningless")
        meaningless = meaningless_dict.get("meaningless", [])

        for word in meaningless:
            text = text.replace(word, "")

        return text

    def _remove_unless_words(self, items: List[str]) -> List[str]:
        """
        Remove items that are only a single character long or match unwanted patterns.
        :param items: list of strings
        :return: list of strings with unwanted items removed
        """
        unwanted_patterns = [r"^\d+$", r"\d+年", r"\d+月", r"\d+日", r"\d+分"]

        def is_unwanted(item):
            return any(re.search(pattern, item) for pattern in unwanted_patterns)

        return [item for item in items if len(item) > 1 and not is_unwanted(item)]
