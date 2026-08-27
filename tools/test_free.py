# -*- coding: utf-8 -*-
"""验证改造后的免费通道: 直接 import engine, 确认默认走 Pollinations, 实测出图。"""
import asyncio, sys, io
sys.path.insert(0, "engine")
import engine

print("IMAGE_PROVIDER =", engine.IMAGE_PROVIDER)
print("prov_available flux      =", engine._prov_available("flux"))
print("prov_available gemini    =", engine._prov_available("gemini"))
print("prov_available cloudflare=", engine._prov_available("cloudflare"))
print("has gen_gemini    =", callable(engine.gen_gemini))
print("has gen_cloudflare=", callable(engine.gen_cloudflare))
print("has gen_flux      =", callable(engine.gen_flux))

async def main():
    print("\n>>> 实测默认免费通道 (Pollinations FLUX + enhance) ...")
    data = await engine.gen_artwork("a minimalist red apple on white studio background, product photography", "square")
    if data:
        out = "research/samples2/free_test_square.png"
        open(out, "wb").write(data)
        print(f"OK 出图成功 -> {out}  ({len(data)//1024} KB)  provider={engine.LAST_PROVIDER}")
    else:
        print("FAIL 默认免费通道未出图")

asyncio.run(main())
