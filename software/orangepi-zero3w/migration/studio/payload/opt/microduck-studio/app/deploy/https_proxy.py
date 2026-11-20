"""Serve the existing Studio on a second, TLS protected LAN port.

By default, connect back to the local address that accepted the client. This
preserves Studio's IP Host check. A loopback-only development Studio can opt in
to the loopback target after explicitly allowing the device IP via --name.
"""
import argparse
import asyncio
import ssl
from pathlib import Path


async def relay(reader, writer):
    try:
        while data := await reader.read(65536):
            writer.write(data)
            await writer.drain()
    except (ConnectionError, OSError):
        pass


async def handle(client_reader, client_writer, target_port, password_file, loopback_target=False):
    backend_writer = None
    try:
        # The old Studio can run without a password. Never expose that mode on
        # the HTTPS listener, including if the password is removed later.
        if not password_file.is_file():
            return
        local_address = ('127.0.0.1' if loopback_target else
                         client_writer.get_extra_info("sockname")[0])
        backend_reader, backend_writer = await asyncio.wait_for(
            asyncio.open_connection(local_address, target_port), timeout=3)
        upstream = asyncio.create_task(relay(client_reader, backend_writer))
        downstream = asyncio.create_task(relay(backend_reader, client_writer))
        done, pending = await asyncio.wait(
            (upstream, downstream), return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*done, *pending, return_exceptions=True)
    except (ConnectionError, OSError, asyncio.TimeoutError):
        pass
    finally:
        client_writer.close()
        closers = [client_writer.wait_closed()]
        if backend_writer is not None:
            backend_writer.close()
            closers.append(backend_writer.wait_closed())
        await asyncio.gather(*closers, return_exceptions=True)


async def serve(args):
    if not Path(args.password_file).is_file():
        raise SystemExit("Studio 还没有登录密码，HTTPS 入口未启动")
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.cert, args.key)
    server = await asyncio.start_server(
        lambda r, w: handle(r, w, args.target_port, Path(args.password_file),
                            args.loopback_target),
        args.listen, args.port, ssl=context, ssl_handshake_timeout=5)
    async with server:
        await server.serve_forever()


def main():
    parser = argparse.ArgumentParser(description="Studio 局域网 HTTPS 入口")
    parser.add_argument("--listen", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8444)
    parser.add_argument("--target-port", type=int, default=8000)
    parser.add_argument("--loopback-target", action="store_true",
                        help="仅用于本机测试 Studio；它须通过 --name 允许鸭哥 IP")
    parser.add_argument("--password-file", default="/var/lib/microduck-studio/password.json")
    parser.add_argument("--cert", required=True)
    parser.add_argument("--key", required=True)
    args = parser.parse_args()
    if not all(1 <= port <= 65535 for port in (args.port, args.target_port)):
        parser.error("端口必须在 1 到 65535 之间")
    asyncio.run(serve(args))


if __name__ == "__main__":
    main()
