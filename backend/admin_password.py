"""交互生成管理员密码摘要；明文不进入命令行参数或输出。"""

from getpass import getpass

from pwdlib import PasswordHash


def main():
    password = getpass("设置管理员密码（至少 12 字符）：")
    if len(password) < 12 or len(password) > 256:
        raise SystemExit("密码长度必须为 12–256 字符")
    if password != getpass("再次输入密码："):
        raise SystemExit("两次输入的密码不一致")
    print("将下面的摘要写入后端环境文件，不要写入前端：")
    print("ADMIN_PASSWORD_HASH='" + PasswordHash.recommended().hash(password) + "'")


if __name__ == "__main__":
    main()
