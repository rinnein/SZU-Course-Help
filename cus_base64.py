#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
自定义 Base64 编码/解码模块

本模块是深大选课系统前端 jQuery.base64 的 Python 等价实现。
用于将 DES 加密后的密码进行 Base64 编码，生成最终的 loginPwd 参数。

调用链: 明文密码 -> desencode.str_enc() -> cus_base64.encode() -> loginPwd
"""


class CustomBase64:
    """
    自定义Base64编码/解码类，支持UTF-8字符，对应原JavaScript的jQuery.base64实现
    """

    def __init__(self):
        # Base64字符集
        self.alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"
        # 构建字符到索引的映射
        self.char_to_index = {char: idx for idx, char in enumerate(self.alphabet)}

    def utf8_encode(self, s):
        """
        将Unicode字符串编码为UTF-8字节序列，对应原JavaScript的c.encode
        """
        return s.encode('utf-8')

    def utf8_decode(self, bytes_data):
        """
        将UTF-8字节序列解码为Unicode字符串，对应原JavaScript的c.decode
        """
        return bytes_data.decode('utf-8')

    def _binary_transform(self, data, is_encode, input_bits, output_bits):
        """
        核心二进制转换函数，对应原JavaScript的e函数
        处理从input_bits位到output_bits位的转换
        """
        result = []
        accumulator = 0
        bits_available = 0

        for byte in data:
            # 将字节转换为无符号整数
            byte = byte & 0xFF if isinstance(byte, int) else ord(byte)
            accumulator = (accumulator << input_bits) | byte
            bits_available += input_bits

            while bits_available >= output_bits:
                bits_available -= output_bits
                # 从高位提取output_bits位
                value = (accumulator >> bits_available) & ((1 << output_bits) - 1)
                result.append(value)
                accumulator &= (1 << bits_available) - 1  # 清除已提取的位

        # 处理剩余的位（仅在编码时）
        if is_encode and bits_available > 0:
            # 左移补0
            value = (accumulator << (output_bits - bits_available)) & ((1 << output_bits) - 1)
            result.append(value)

        return result

    def encode(self, s, raw=False):
        """
        Base64编码，对应原JavaScript的$.base64.encode
        :param s: 要编码的字符串
        :param raw: 是否不进行UTF-8编码（默认False）
        :return: Base64编码后的字符串
        """
        # 处理UTF-8编码
        if not raw:
            bytes_data = self.utf8_encode(s)
        else:
            bytes_data = s if isinstance(s, bytes) else s.encode('latin-1')

        # 将8位字节转换为6位值
        six_bit_values = self._binary_transform(bytes_data, True, 8, 6)

        # 转换为Base64字符
        base64_chars = [self.alphabet[value] for value in six_bit_values]
        encoded = ''.join(base64_chars)

        # 添加填充符=
        padding = (4 - (len(encoded) % 4)) % 4
        encoded += '=' * padding

        return encoded

    def decode(self, s, raw=False):
        """
        Base64解码，对应原JavaScript的$.base64.decode
        :param s: 要解码的Base64字符串
        :param raw: 是否不进行UTF-8解码（默认False）
        :return: 解码后的字符串
        """
        # 移除填充符
        s = s.replace('=', '')
        if not s:
            return '' if not raw else b''

        # 将Base64字符转换为6位值
        six_bit_values = []
        for char in s:
            if char in self.char_to_index:
                six_bit_values.append(self.char_to_index[char])

        # 将6位值转换为8位字节
        byte_values = self._binary_transform(six_bit_values, False, 6, 8)
        bytes_data = bytes(byte_values)

        # 处理UTF-8解码
        if not raw:
            return self.utf8_decode(bytes_data)
        return bytes_data


# 测试代码
if __name__ == "__main__":
    base64 = CustomBase64()

    # 测试基本功能
    test_str = "525E54AA6F9EDC41B8F0C4603FA348E4AB53C103F3C99D03"
    encoded = base64.encode(test_str)
    decoded = base64.decode(encoded)
    print(f"基本测试:")
    print(f"原始字符串: {test_str}")
    print(f"编码后: {encoded}")
    print(f"解码后: {decoded}")
    print(f"是否一致: {test_str == decoded}\n")

    # 测试中文支持
    chinese_str = "你好，世界！这是一个测试。"
    encoded_chinese = base64.encode(chinese_str)
    decoded_chinese = base64.decode(encoded_chinese)
    print(f"中文测试:")
    print(f"原始字符串: {chinese_str}")
    print(f"编码后: {encoded_chinese}")
    print(f"解码后: {decoded_chinese}")
    print(f"是否一致: {chinese_str == decoded_chinese}")
