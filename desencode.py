"""
DES 密码加密模块（从 JavaScript 移植的 Python 实现）

本模块是深大选课系统前端 des.js 的 Python 等价实现，
用于将用户密码加密后提交给登录接口。

加密流程:
    明文密码 -> DES三重加密(key1="this", key2="password", key3="is") -> 十六进制字符串

技术说明:
    - 这是一个自定义的 DES 实现（非标准库），从学校前端 JS 代码逆向移植
    - 加密函数 str_enc() 支持1~3组密钥的多轮加密
    - 每4个字符为一个分组，转为64位二进制后经过标准DES的16轮Feistel变换
    - 包含标准DES的所有组件: 初始置换、扩展置换、S盒、P置换、最终置换

【重要】本模块为密码加密核心逻辑，禁止修改加密算法实现
"""


def str_enc(r, e=None, a=None, n=None):
    """字符串加密函数（对应JS的 strEnc）"""
    i = len(r)
    u = ""

    # 获取密钥字节
    if e is not None and e != "":
        t = get_key_bytes(e)
        c = len(t)
    if a is not None and a != "":
        s = get_key_bytes(a)
        f = len(s)
    if n is not None and n != "":
        o = get_key_bytes(n)
        m = len(o)

    if i > 0:
        if i < 4:
            l = str_to_bt(r)
            if e is not None and e != "" and a is not None and a != "" and n is not None and n != "":
                j = l
                for b in range(c):
                    j = enc(j, t[b])
                for h in range(f):
                    j = enc(j, s[h])
                for x in range(m):
                    j = enc(j, o[x])
                A = j
            elif e is not None and e != "" and a is not None and a != "":
                j = l
                for b in range(c):
                    j = enc(j, t[b])
                for h in range(f):
                    j = enc(j, s[h])
                A = j
            elif e is not None and e != "":
                j = l
                for b in range(c):
                    j = enc(j, t[b])
                A = j
            u = bt64_to_hex(A)
        else:
            k = i // 4
            y = i % 4

            for v in range(k):
                g = r[4 * v:4 * v + 4]
                w = str_to_bt(g)
                if e is not None and e != "" and a is not None and a != "" and n is not None and n != "":
                    j = w
                    for b in range(c):
                        j = enc(j, t[b])
                    for h in range(f):
                        j = enc(j, s[h])
                    for x in range(m):
                        j = enc(j, o[x])
                    A = j
                elif e is not None and e != "" and a is not None and a != "":
                    j = w
                    for b in range(c):
                        j = enc(j, t[b])
                    for h in range(f):
                        j = enc(j, s[h])
                    A = j
                elif e is not None and e != "":
                    j = w
                    for b in range(c):
                        j = enc(j, t[b])
                    A = j
                u += bt64_to_hex(A)

            if y > 0:
                B = r[4 * k:i]
                w = str_to_bt(B)
                if e is not None and e != "" and a is not None and a != "" and n is not None and n != "":
                    j = w
                    for b in range(c):
                        j = enc(j, t[b])
                    for h in range(f):
                        j = enc(j, s[h])
                    for x in range(m):
                        j = enc(j, o[x])
                    A = j
                elif e is not None and e != "" and a is not None and a != "":
                    j = w
                    for b in range(c):
                        j = enc(j, t[b])
                    for h in range(f):
                        j = enc(j, s[h])
                    A = j
                elif e is not None and e != "":
                    j = w
                    for b in range(c):
                        j = enc(j, t[b])
                    A = j
                u += bt64_to_hex(A)

    return u


def str_dec(r, e=None, a=None, n=None):
    """字符串解密函数"""
    i = len(r)
    u = ""

    # 获取密钥字节
    if e is not None and e != "":
        t = get_key_bytes(e)
        c = len(t)
    if a is not None and a != "":
        s = get_key_bytes(a)
        f = len(s)
    if n is not None and n != "":
        o = get_key_bytes(n)
        m = len(o)

    l = i // 16

    for b in range(l):
        y = r[16 * b:16 * b + 16]
        v = hex_to_bt64(y)
        g = [0] * 64
        for w in range(64):
            g[w] = int(v[w])

        if e is not None and e != "" and a is not None and a != "" and n is not None and n != "":
            A = g
            for B in range(m - 1, -1, -1):
                A = dec(A, o[B])
            for x in range(f - 1, -1, -1):
                A = dec(A, s[x])
            for h in range(c - 1, -1, -1):
                A = dec(A, t[h])
            k = A
        elif e is not None and e != "" and a is not None and a != "":
            A = g
            for B in range(f - 1, -1, -1):
                A = dec(A, s[B])
            for x in range(c - 1, -1, -1):
                A = dec(A, t[x])
            k = A
        elif e is not None and e != "":
            A = g
            for B in range(c - 1, -1, -1):
                A = dec(A, t[B])
            k = A

        u += byte_to_string(k)

    return u


def get_key_bytes(r):
    """获取密钥字节"""
    e = []
    a = len(r)
    n = a // 4
    t = a % 4

    for s in range(n):
        e.append(str_to_bt(r[4 * s:4 * s + 4]))

    if t > 0:
        e.append(str_to_bt(r[4 * n:a]))

    return e


def str_to_bt(r):
    """字符串转二进制"""
    e = len(r)
    a = [0] * 64

    if e < 4:
        for n in range(e):
            c = ord(r[n])
            for t in range(16):
                f = 1
                for m in range(15, t, -1):
                    f *= 2
                a[16 * n + t] = int(c // f) % 2

        for s in range(e, 4):
            c = 0
            for o in range(16):
                f = 1
                for m in range(15, o, -1):
                    f *= 2
                a[16 * s + o] = int(c // f) % 2
    else:
        for n in range(4):
            c = ord(r[n])
            for t in range(16):
                f = 1
                for m in range(15, t, -1):
                    f *= 2
                a[16 * n + t] = int(c // f) % 2

    return a


def bt4_to_hex(r):
    """4位二进制转十六进制"""
    hex_map = {
        "0000": "0", "0001": "1", "0010": "2", "0011": "3",
        "0100": "4", "0101": "5", "0110": "6", "0111": "7",
        "1000": "8", "1001": "9", "1010": "A", "1011": "B",
        "1100": "C", "1101": "D", "1110": "E", "1111": "F"
    }
    return hex_map.get(r, "")


def hex_to_bt4(r):
    """十六进制转4位二进制"""
    bt_map = {
        "0": "0000", "1": "0001", "2": "0010", "3": "0011",
        "4": "0100", "5": "0101", "6": "0110", "7": "0111",
        "8": "1000", "9": "1001", "A": "1010", "B": "1011",
        "C": "1100", "D": "1101", "E": "1110", "F": "1111"
    }
    return bt_map.get(r, "")


def byte_to_string(r):
    """字节转字符串"""
    e = ""
    for i in range(4):
        a = 0
        for j in range(16):
            n = 1
            for m in range(15, j, -1):
                n *= 2
            a += r[16 * i + j] * n
        if a != 0:
            e += chr(a)
    return e


def bt64_to_hex(r):
    """64位二进制转十六进制"""
    e = ""
    for i in range(16):
        a = ""
        for j in range(4):
            a += str(r[4 * i + j])
        e += bt4_to_hex(a)
    return e


def hex_to_bt64(r):
    """十六进制转64位二进制"""
    e = ""
    for i in range(16):
        e += hex_to_bt4(r[i])
    return e


def enc(r, e):
    """加密函数"""
    a = generate_keys(e)
    n = init_permute(r)
    t = [0] * 32
    s = [0] * 32
    o = [0] * 32

    for m in range(32):
        t[m] = n[m]
        s[m] = n[32 + m]

    for c in range(16):
        for f in range(32):
            o[f] = t[f]
            t[f] = s[f]

        l = [0] * 48
        for i in range(48):
            l[i] = a[c][i]

        b = xor(p_permute(s_box_permute(xor(expand_permute(s), l))), o)
        for u in range(32):
            s[u] = b[u]

    k = [0] * 64
    for c in range(32):
        k[c] = s[c]
        k[32 + c] = t[c]

    return finally_permute(k)


def dec(r, e):
    """解密函数"""
    a = generate_keys(e)
    n = init_permute(r)
    t = [0] * 32
    s = [0] * 32
    o = [0] * 32

    for m in range(32):
        t[m] = n[m]
        s[m] = n[32 + m]

    for c in range(15, -1, -1):
        for f in range(32):
            o[f] = t[f]
            t[f] = s[f]

        l = [0] * 48
        for i in range(48):
            l[i] = a[c][i]

        b = xor(p_permute(s_box_permute(xor(expand_permute(s), l))), o)
        for u in range(32):
            s[u] = b[u]

    k = [0] * 64
    for c in range(32):
        k[c] = s[c]
        k[32 + c] = t[c]

    return finally_permute(k)


def init_permute(r):
    """初始置换"""
    e = [0] * 64
    for i in range(4):
        m = 1 + 2 * i
        n = 2 * i
        for j in range(7, -1, -1):
            k = 7 - j
            e[8 * i + k] = r[8 * j + m]
            e[8 * i + k + 32] = r[8 * j + n]
    return e


def expand_permute(r):
    """扩展置换"""
    e = [0] * 48
    for i in range(8):
        if i == 0:
            e[6 * i + 0] = r[31]
        else:
            e[6 * i + 0] = r[4 * i - 1]

        e[6 * i + 1] = r[4 * i + 0]
        e[6 * i + 2] = r[4 * i + 1]
        e[6 * i + 3] = r[4 * i + 2]
        e[6 * i + 4] = r[4 * i + 3]

        if i == 7:
            e[6 * i + 5] = r[0]
        else:
            e[6 * i + 5] = r[4 * i + 4]

    return e


def xor(r, e):
    """异或运算"""
    a = [0] * len(r)
    for i in range(len(r)):
        a[i] = r[i] ^ e[i]
    return a


def s_box_permute(r):
    """S盒置换"""
    e = [0] * 32

    # S盒定义
    s_boxes = [
        # S1
        [[14, 4, 13, 1, 2, 15, 11, 8, 3, 10, 6, 12, 5, 9, 0, 7],
         [0, 15, 7, 4, 14, 2, 13, 1, 10, 6, 12, 11, 9, 5, 3, 8],
         [4, 1, 14, 8, 13, 6, 2, 11, 15, 12, 9, 7, 3, 10, 5, 0],
         [15, 12, 8, 2, 4, 9, 1, 7, 5, 11, 3, 14, 10, 0, 6, 13]],
        # S2
        [[15, 1, 8, 14, 6, 11, 3, 4, 9, 7, 2, 13, 12, 0, 5, 10],
         [3, 13, 4, 7, 15, 2, 8, 14, 12, 0, 1, 10, 6, 9, 11, 5],
         [0, 14, 7, 11, 10, 4, 13, 1, 5, 8, 12, 6, 9, 3, 2, 15],
         [13, 8, 10, 1, 3, 15, 4, 2, 11, 6, 7, 12, 0, 5, 14, 9]],
        # S3
        [[10, 0, 9, 14, 6, 3, 15, 5, 1, 13, 12, 7, 11, 4, 2, 8],
         [13, 7, 0, 9, 3, 4, 6, 10, 2, 8, 5, 14, 12, 11, 15, 1],
         [13, 6, 4, 9, 8, 15, 3, 0, 11, 1, 2, 12, 5, 10, 14, 7],
         [1, 10, 13, 0, 6, 9, 8, 7, 4, 15, 14, 3, 11, 5, 2, 12]],
        # S4
        [[7, 13, 14, 3, 0, 6, 9, 10, 1, 2, 8, 5, 11, 12, 4, 15],
         [13, 8, 11, 5, 6, 15, 0, 3, 4, 7, 2, 12, 1, 10, 14, 9],
         [10, 6, 9, 0, 12, 11, 7, 13, 15, 1, 3, 14, 5, 2, 8, 4],
         [3, 15, 0, 6, 10, 1, 13, 8, 9, 4, 5, 11, 12, 7, 2, 14]],
        # S5
        [[2, 12, 4, 1, 7, 10, 11, 6, 8, 5, 3, 15, 13, 0, 14, 9],
         [14, 11, 2, 12, 4, 7, 13, 1, 5, 0, 15, 10, 3, 9, 8, 6],
         [4, 2, 1, 11, 10, 13, 7, 8, 15, 9, 12, 5, 6, 3, 0, 14],
         [11, 8, 12, 7, 1, 14, 2, 13, 6, 15, 0, 9, 10, 4, 5, 3]],
        # S6
        [[12, 1, 10, 15, 9, 2, 6, 8, 0, 13, 3, 4, 14, 7, 5, 11],
         [10, 15, 4, 2, 7, 12, 9, 5, 6, 1, 13, 14, 0, 11, 3, 8],
         [9, 14, 15, 5, 2, 8, 12, 3, 7, 0, 4, 10, 1, 13, 11, 6],
         [4, 3, 2, 12, 9, 5, 15, 10, 11, 14, 1, 7, 6, 0, 8, 13]],
        # S7
        [[4, 11, 2, 14, 15, 0, 8, 13, 3, 12, 9, 7, 5, 10, 6, 1],
         [13, 0, 11, 7, 4, 9, 1, 10, 14, 3, 5, 12, 2, 15, 8, 6],
         [1, 4, 11, 13, 12, 3, 7, 14, 10, 15, 6, 8, 0, 5, 9, 2],
         [6, 11, 13, 8, 1, 4, 10, 7, 9, 5, 0, 15, 14, 2, 3, 12]],
        # S8
        [[13, 2, 8, 4, 6, 15, 11, 1, 10, 9, 3, 14, 5, 0, 12, 7],
         [1, 15, 13, 8, 10, 3, 7, 4, 12, 5, 6, 11, 0, 14, 9, 2],
         [7, 11, 4, 1, 9, 12, 14, 2, 0, 6, 10, 13, 15, 3, 5, 8],
         [2, 1, 14, 7, 4, 10, 8, 13, 15, 12, 9, 0, 3, 5, 6, 11]]
    ]

    for m in range(8):
        l = 2 * r[6 * m + 0] + r[6 * m + 5]
        b = 2 * r[6 * m + 1] * 2 * 2 + 2 * r[6 * m + 2] * 2 + 2 * r[6 * m + 3] + r[6 * m + 4]

        a = get_box_binary(s_boxes[m][l][b])
        e[4 * m + 0] = int(a[0])
        e[4 * m + 1] = int(a[1])
        e[4 * m + 2] = int(a[2])
        e[4 * m + 3] = int(a[3])

    return e


def p_permute(r):
    """P置换"""
    e = [0] * 32
    e[0] = r[15]; e[1] = r[6]; e[2] = r[19]; e[3] = r[20]
    e[4] = r[28]; e[5] = r[11]; e[6] = r[27]; e[7] = r[16]
    e[8] = r[0]; e[9] = r[14]; e[10] = r[22]; e[11] = r[25]
    e[12] = r[4]; e[13] = r[17]; e[14] = r[30]; e[15] = r[9]
    e[16] = r[1]; e[17] = r[7]; e[18] = r[23]; e[19] = r[13]
    e[20] = r[31]; e[21] = r[26]; e[22] = r[2]; e[23] = r[8]
    e[24] = r[18]; e[25] = r[12]; e[26] = r[29]; e[27] = r[5]
    e[28] = r[21]; e[29] = r[10]; e[30] = r[3]; e[31] = r[24]
    return e


def finally_permute(r):
    """最终置换"""
    e = [0] * 64
    e[0] = r[39]; e[1] = r[7]; e[2] = r[47]; e[3] = r[15]
    e[4] = r[55]; e[5] = r[23]; e[6] = r[63]; e[7] = r[31]
    e[8] = r[38]; e[9] = r[6]; e[10] = r[46]; e[11] = r[14]
    e[12] = r[54]; e[13] = r[22]; e[14] = r[62]; e[15] = r[30]
    e[16] = r[37]; e[17] = r[5]; e[18] = r[45]; e[19] = r[13]
    e[20] = r[53]; e[21] = r[21]; e[22] = r[61]; e[23] = r[29]
    e[24] = r[36]; e[25] = r[4]; e[26] = r[44]; e[27] = r[12]
    e[28] = r[52]; e[29] = r[20]; e[30] = r[60]; e[31] = r[28]
    e[32] = r[35]; e[33] = r[3]; e[34] = r[43]; e[35] = r[11]
    e[36] = r[51]; e[37] = r[19]; e[38] = r[59]; e[39] = r[27]
    e[40] = r[34]; e[41] = r[2]; e[42] = r[42]; e[43] = r[10]
    e[44] = r[50]; e[45] = r[18]; e[46] = r[58]; e[47] = r[26]
    e[48] = r[33]; e[49] = r[1]; e[50] = r[41]; e[51] = r[9]
    e[52] = r[49]; e[53] = r[17]; e[54] = r[57]; e[55] = r[25]
    e[56] = r[32]; e[57] = r[0]; e[58] = r[40]; e[59] = r[8]
    e[60] = r[48]; e[61] = r[16]; e[62] = r[56]; e[63] = r[24]
    return e


def get_box_binary(r):
    """获取S盒输出的二进制表示"""
    binary_map = {
        0: "0000", 1: "0001", 2: "0010", 3: "0011",
        4: "0100", 5: "0101", 6: "0110", 7: "0111",
        8: "1000", 9: "1001", 10: "1010", 11: "1011",
        12: "1100", 13: "1101", 14: "1110", 15: "1111"
    }
    return binary_map.get(r, "0000")


def generate_keys(r):
    """生成16轮子密钥"""
    e = [0] * 56
    a = [[0] * 48 for _ in range(16)]

    # 左移次数表
    n = [1, 1, 2, 2, 2, 2, 2, 2, 1, 2, 2, 2, 2, 2, 2, 1]

    # PC-1置换
    for t in range(7):
        for j in range(8):
            k = 7 - j
            e[8 * t + j] = r[8 * k + t]

    # 生成16轮子密钥
    for t in range(16):
        # 左移操作
        for j in range(n[t]):
            s = e[0]
            o = e[28]
            for k in range(27):
                e[k] = e[k + 1]
                e[28 + k] = e[29 + k]
            e[27] = s
            e[55] = o

        # PC-2置换
        c = [0] * 48
        c[0] = e[13]; c[1] = e[16]; c[2] = e[10]; c[3] = e[23]
        c[4] = e[0]; c[5] = e[4]; c[6] = e[2]; c[7] = e[27]
        c[8] = e[14]; c[9] = e[5]; c[10] = e[20]; c[11] = e[9]
        c[12] = e[22]; c[13] = e[18]; c[14] = e[11]; c[15] = e[3]
        c[16] = e[25]; c[17] = e[7]; c[18] = e[15]; c[19] = e[6]
        c[20] = e[26]; c[21] = e[19]; c[22] = e[12]; c[23] = e[1]
        c[24] = e[40]; c[25] = e[51]; c[26] = e[30]; c[27] = e[36]
        c[28] = e[46]; c[29] = e[54]; c[30] = e[29]; c[31] = e[39]
        c[32] = e[50]; c[33] = e[44]; c[34] = e[32]; c[35] = e[47]
        c[36] = e[43]; c[37] = e[48]; c[38] = e[38]; c[39] = e[55]
        c[40] = e[33]; c[41] = e[52]; c[42] = e[45]; c[43] = e[41]
        c[44] = e[49]; c[45] = e[35]; c[46] = e[28]; c[47] = e[31]

        for m in range(48):
            a[t][m] = c[m]

    return a


# 为了兼容性，提供与JavaScript相同的函数名
def strEnc(r, e=None, a=None, n=None):
    """兼容性函数名"""
    return str_enc(r, e, a, n)


def strDec(r, e=None, a=None, n=None):
    """兼容性函数名"""
    return str_dec(r, e, a, n)


if __name__ == "__main__":
    # 测试代码
    test_string = "cjl061202"

    # 加密
    encrypted = str_enc(test_string, "this", "password", "is")
    print(f"原文: {test_string}")

    print(f"加密结果: {encrypted}")
