export type PlatformCaseReview = {
  id: string; title: string; product: string; status: "completed" | "failed" | "processing" | "partial";
  benefit: string; inputs: { src: string; label: string }[]; result?: string;
  results?: { src: string; label: string; width: number; height: number }[];
  historyDate?: string; additionalInputs?: { src: string; label: string }[];
  taskUrl: string; taskId: string; settings: string; note: string;
};

export const PLATFORM_CASE_REVIEW: PlatformCaseReview[] = [
  {
    "id": "replica",
    "title": "一比一复刻",
    "product": "OLAY 超白瓶精华身体乳",
    "status": "completed",
    "benefit": "用户提供的 3 张商品实拍＋7 张构图参考，经网站实际生成 8 张主图与详情画面。",
    "inputs": [
      {
        "src": "/images/cases/olay-20261030/product-01.webp",
        "label": "本次商品原图 1"
      },
      {
        "src": "/images/cases/olay-20261030/reference-01.jpg",
        "label": "参考：reference.jpg"
      }
    ],
    "additionalInputs": [
      {
        "src": "/images/cases/olay-20261030/product-02.webp",
        "label": "本次商品原图 2"
      },
      {
        "src": "/images/cases/olay-20261030/product-03.webp",
        "label": "本次商品原图 3"
      },
      {
        "src": "/images/cases/olay-20261030/reference-02.webp",
        "label": "参考：reference (1).jpg"
      },
      {
        "src": "/images/cases/olay-20261030/reference-03.webp",
        "label": "参考：reference (2).jpg"
      },
      {
        "src": "/images/cases/olay-20261030/reference-04.webp",
        "label": "参考：reference (3).jpg"
      },
      {
        "src": "/images/cases/olay-20261030/reference-05.webp",
        "label": "参考：reference (5).jpg"
      },
      {
        "src": "/images/cases/olay-20261030/reference-06.webp",
        "label": "参考：reference (6).jpg"
      },
      {
        "src": "/images/cases/olay-20261030/reference-07.webp",
        "label": "参考：reference (7).jpg"
      }
    ],
    "results": [
      {
        "src": "/images/cases/olay-20261030/replica-01.png",
        "width": 1254,
        "height": 1254,
        "label": "成图 1 · 参考 1"
      },
      {
        "src": "/images/cases/olay-20261030/replica-02.png",
        "width": 1254,
        "height": 1254,
        "label": "成图 2 · 参考 2"
      },
      {
        "src": "/images/cases/olay-20261030/replica-03.png",
        "width": 959,
        "height": 1640,
        "label": "成图 3 · 参考 3"
      },
      {
        "src": "/images/cases/olay-20261030/replica-04.png",
        "width": 958,
        "height": 1642,
        "label": "成图 4 · 参考 4"
      },
      {
        "src": "/images/cases/olay-20261030/replica-05.png",
        "width": 1072,
        "height": 1467,
        "label": "成图 5 · 参考 5 · 上屏"
      },
      {
        "src": "/images/cases/olay-20261030/replica-06.png",
        "width": 1578,
        "height": 996,
        "label": "成图 6 · 参考 5 · 下屏"
      },
      {
        "src": "/images/cases/olay-20261030/replica-07.png",
        "width": 1055,
        "height": 1491,
        "label": "成图 7 · 参考 6"
      },
      {
        "src": "/images/cases/olay-20261030/replica-08.png",
        "width": 1112,
        "height": 1415,
        "label": "成图 8 · 参考 7"
      }
    ],
    "taskId": "rep_65fbeeab9297436da8969ee5ecf16132",
    "taskUrl": "https://huipin.haiyushuke.com/style-replica?sessionId=rep_65fbeeab9297436da8969ee5ecf16132",
    "historyDate": "2026-10-30 12:57",
    "settings": "一比一模仿 · 快速版 · 天猫 · 简体中文。3 张商品图、7 张参考图，使用用户提供的卖点；第 5 张参考长图拆成两屏。参考的品牌、实验百分比、价格及赠品信息不作为 OLAY 商品事实输入。",
    "note": "本轮未查到旧记录后，通过网站功能新建并完成，8 / 8 张已交付。10 张输入与 8 张输出均保留网站原文件，与用户原始输入及网站输出的哈希逐一核对一致。部分成图的泵头朝向、瓶身小字与原图仍有差异，保留供确认。"
  },
  {
    "id": "replica-style",
    "title": "风格复刻",
    "product": "聪颖鲜肉全价猫粮",
    "status": "completed",
    "benefit": "商品包装实拍＋其他商品的视觉参考，查看同款包装如何形成新的画面。",
    "inputs": [
      {
        "src": "/images/cases/history-20261030/asset_2ff03b4f5f0e44caa4482ed4fbd7bbe2.webp",
        "label": "历史商品原图 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_c1c0ef252b6a49e4b58d641ed6d5fca3.webp",
        "label": "历史构图参考 1"
      }
    ],
    "additionalInputs": [
      {
        "src": "/images/cases/history-20261030/asset_cc69942a44ef487e90156004859d9fd3.webp",
        "label": "历史商品原图 1"
      },
      {
        "src": "/images/cases/history-20261030/asset_4f73d17dfcc8437795b38609f7f81540.webp",
        "label": "历史构图参考 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_1847a5bb129c448b9d28e7f9170acb49.webp",
        "label": "历史构图参考 3"
      },
      {
        "src": "/images/cases/history-20261030/asset_1129a868429548b2b83ce67f5b8ae62c.webp",
        "label": "历史构图参考 4"
      },
      {
        "src": "/images/cases/history-20261030/asset_e4824334953d424ca2595dc6258b9ddb.webp",
        "label": "历史构图参考 5"
      },
      {
        "src": "/images/cases/history-20261030/asset_1029027cb7e34bc58fe300acb8c96d82.webp",
        "label": "历史构图参考 6"
      },
      {
        "src": "/images/cases/history-20261030/asset_5d0bfdda36ae44eca1d887a49ca78bf2.webp",
        "label": "历史构图参考 7"
      },
      {
        "src": "/images/cases/history-20261030/asset_033c30fd74174e4c81dd4e0e456cb0c6.webp",
        "label": "历史构图参考 8"
      },
      {
        "src": "/images/cases/history-20261030/asset_442a3cb556014d7ea126bfa66fbabf12.webp",
        "label": "历史构图参考 9"
      },
      {
        "src": "/images/cases/history-20261030/asset_7c20e72d4d294470882f850917368879.webp",
        "label": "历史构图参考 10"
      },
      {
        "src": "/images/cases/history-20261030/asset_6f8cac9a898e449a8817ceabb050f598.webp",
        "label": "历史构图参考 11"
      }
    ],
    "results": [
      {
        "src": "/images/cases/history-20261030/asset_796e4b4646c942299b63ceb2ed373cd6.png",
        "width": 1254,
        "height": 1254,
        "label": "风格复刻 1"
      },
      {
        "src": "/images/cases/history-20261030/asset_252b160318f446da85c965626469c7ce.png",
        "width": 1254,
        "height": 1254,
        "label": "风格复刻 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_fc6f46e4353748a8bac9cbddcc6572c8.png",
        "width": 1254,
        "height": 1254,
        "label": "风格复刻 3"
      },
      {
        "src": "/images/cases/history-20261030/asset_ecd6bfbe95904de19227c61d1371fb5a.png",
        "width": 1254,
        "height": 1254,
        "label": "风格复刻 4"
      },
      {
        "src": "/images/cases/history-20261030/asset_a071347e2f22431081d5c809632e82b6.png",
        "width": 1254,
        "height": 1254,
        "label": "风格复刻 5"
      },
      {
        "src": "/images/cases/history-20261030/asset_52eb02cd19f341b6a478c07c5bfad2e2.png",
        "width": 1369,
        "height": 1149,
        "label": "风格复刻 6"
      },
      {
        "src": "/images/cases/history-20261030/asset_a337b2108e9e477f92e66b856d148009.png",
        "width": 916,
        "height": 1716,
        "label": "风格复刻 7"
      },
      {
        "src": "/images/cases/history-20261030/asset_d65bcdd3f0354d30800bc54b04eff6ba.png",
        "width": 1059,
        "height": 1486,
        "label": "风格复刻 8"
      },
      {
        "src": "/images/cases/history-20261030/asset_7ab1bf4af60948f7883cabd87d0f08b0.png",
        "width": 1101,
        "height": 1428,
        "label": "风格复刻 9"
      },
      {
        "src": "/images/cases/history-20261030/asset_e259183070294e4a908b5cf87bf06b92.png",
        "width": 1165,
        "height": 1350,
        "label": "风格复刻 10"
      },
      {
        "src": "/images/cases/history-20261030/asset_6b4c79ce2bfe4e2b86e2e584bfb65c33.png",
        "width": 1036,
        "height": 1519,
        "label": "风格复刻 11"
      }
    ],
    "taskId": "rep_084240936384414cbe4c3b8e31b13483",
    "taskUrl": "https://huipin.haiyushuke.com/style-replica?sessionId=rep_084240936384414cbe4c3b8e31b13483",
    "historyDate": "2026-10-29 17:58",
    "settings": "风格复刻 · 快速版 · 天猫 · 简体中文。输入为商品正反面实拍与 11 张参考；完整保留原任务的输出。",
    "note": "提取自 2026-10-29 17:58 的历史任务。2 张商品原图、11 张参考图；已交付 11 / 11 张。原图、参考和结果均直接保留历史文件，未重新生成或二次修图。"
  },
  {
    "id": "detail",
    "title": "详情图套图",
    "product": "聪颖鲜肉全价猫粮",
    "status": "completed",
    "benefit": "商品包装正反面实拍，展开为 8 页包含场景、包装与信息层次的详情图。",
    "inputs": [
      {
        "src": "/images/cases/history-20261030/asset_50341d648b31488eaa8f7696c34a3719.webp",
        "label": "历史商品原图 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_546c7ae776444cb1a788f0398159c69d.webp",
        "label": "历史商品原图 1"
      }
    ],
    "additionalInputs": [],
    "results": [
      {
        "src": "/images/cases/history-20261030/asset_84b0381b6cef439c8486c09ded379678.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 1"
      },
      {
        "src": "/images/cases/history-20261030/asset_6f416209332b45b285e07c3f91a6124b.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_f82998bb1a5c4b5685637cbe7ed89b47.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 3"
      },
      {
        "src": "/images/cases/history-20261030/asset_cfbafadf11ff473595c02a8456c1f3fb.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 4"
      },
      {
        "src": "/images/cases/history-20261030/asset_b4824b3345894565bd3812b697235d68.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 5"
      },
      {
        "src": "/images/cases/history-20261030/asset_09a620ea0cac4910b323579e1f832ceb.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 6"
      },
      {
        "src": "/images/cases/history-20261030/asset_a0860c2108244b5abef3825dac9bac6b.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 7"
      },
      {
        "src": "/images/cases/history-20261030/asset_75ffa036f7c54cb9b370efd100383d1d.png",
        "width": 1086,
        "height": 1448,
        "label": "详情页 8"
      }
    ],
    "taskId": "job_d9af0585a8ab4c85b3f220d892e67bc9",
    "taskUrl": "https://huipin.haiyushuke.com/workspace/detail-pack?jobId=job_d9af0585a8ab4c85b3f220d892e67bc9",
    "historyDate": "2026-10-30 09:44",
    "settings": "历史任务 · 详情快速版 · 天猫 · 简体中文 · 8 页。商品身份与信息来自该任务的正反面素材及原有设置。",
    "note": "提取自 2026-10-30 09:44 的历史任务。2 张商品原图；已交付 8 / 8 张。原图、参考和结果均直接保留历史文件，未重新生成或二次修图。"
  },
  {
    "id": "domestic",
    "title": "国内主图",
    "product": "聪颖鲜肉全价猫粮",
    "status": "completed",
    "benefit": "同一商品的两张实拍素材，查看一张搜索主图与五张轮播图。",
    "inputs": [
      {
        "src": "/images/cases/history-20261030/asset_a4f5437b884645d9bf65c88dccaebad8.webp",
        "label": "历史商品原图 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_60006fea30c54be8814f04f80faefa5b.webp",
        "label": "历史商品原图 1"
      }
    ],
    "additionalInputs": [],
    "results": [
      {
        "src": "/images/cases/history-20261030/asset_a47a47f308464d7ea2e928e8cc9f5c0c.png",
        "width": 1254,
        "height": 1254,
        "label": "主图 1"
      },
      {
        "src": "/images/cases/history-20261030/asset_059495f98f8c4da788a8073d5d29d66e.png",
        "width": 1254,
        "height": 1254,
        "label": "主图 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_7b6f4730d64b4605beadc82ac38d2aed.png",
        "width": 1254,
        "height": 1254,
        "label": "主图 3"
      },
      {
        "src": "/images/cases/history-20261030/asset_7b6a6e06a65d458fbd4d227a4f02a9aa.png",
        "width": 1254,
        "height": 1254,
        "label": "主图 4"
      },
      {
        "src": "/images/cases/history-20261030/asset_0ce8d0527b7244e4930dca5ff5e29692.png",
        "width": 1254,
        "height": 1254,
        "label": "主图 5"
      },
      {
        "src": "/images/cases/history-20261030/asset_ed9ed9ac9e48459ca3914436259154a2.png",
        "width": 1254,
        "height": 1254,
        "label": "主图 6"
      }
    ],
    "taskId": "job_2a9cc99a94f94cbeb7f8e1e190792893",
    "taskUrl": "https://huipin.haiyushuke.com/workspace/domestic-main-image?jobId=job_2a9cc99a94f94cbeb7f8e1e190792893",
    "historyDate": "2026-10-30 10:31",
    "settings": "历史任务 · 国内主图快速版 · 天猫 · 简体中文。1 张搜索主图＋5 张轮播图。",
    "note": "提取自 2026-10-30 10:31 的历史任务。2 张商品原图；已交付 6 / 6 张。原图、参考和结果均直接保留历史文件，未重新生成或二次修图。"
  },
  {
    "id": "amazon",
    "title": "亚马逊套图",
    "product": "聪颖鲜肉全价猫粮",
    "status": "completed",
    "benefit": "两张包装照片，查看白底主图、英文副图和 A+ 横幅的整套历史输出。",
    "inputs": [
      {
        "src": "/images/cases/history-20261030/asset_18546d61a37b4ea8a6b318a854d81daa.webp",
        "label": "历史商品原图 2"
      },
      {
        "src": "/images/cases/history-20261030/asset_0c79e18bcff84fb2bd8b52f118e536e0.webp",
        "label": "历史商品原图 1"
      }
    ],
    "additionalInputs": [],
    "results": [
      {
        "src": "/images/cases/history-20261030/asset_20b9cb76cf2e4be2b84475b3ce21cd71.png",
        "width": 1254,
        "height": 1254,
        "label": "IMAGE-001"
      },
      {
        "src": "/images/cases/history-20261030/asset_81403ceb4b3641ba89b34f25e8aa6735.png",
        "width": 1086,
        "height": 1448,
        "label": "IMAGE-002"
      },
      {
        "src": "/images/cases/history-20261030/asset_f99178eda6aa4cc8a9b275dd4de80c0d.png",
        "width": 1086,
        "height": 1448,
        "label": "IMAGE-003"
      },
      {
        "src": "/images/cases/history-20261030/asset_fd14677482fa416496dd4a6f716f5949.png",
        "width": 1086,
        "height": 1448,
        "label": "IMAGE-004"
      },
      {
        "src": "/images/cases/history-20261030/asset_6be242c1fc7047e89bc0883a6b856ee3.png",
        "width": 1086,
        "height": 1448,
        "label": "IMAGE-005"
      },
      {
        "src": "/images/cases/history-20261030/asset_161a79b681c04d908d7acf3e3f083a64.png",
        "width": 1086,
        "height": 1448,
        "label": "IMAGE-006"
      },
      {
        "src": "/images/cases/history-20261030/asset_8abceec820494f9ebd90a3eca9360b6d.png",
        "width": 1086,
        "height": 1448,
        "label": "IMAGE-007"
      },
      {
        "src": "/images/cases/history-20261030/asset_9bf5729d308b424ca01820f0d02aa004.png",
        "width": 1958,
        "height": 803,
        "label": "IMAGE-008"
      },
      {
        "src": "/images/cases/history-20261030/asset_42ac4ec335ba4e1791a3b6a032ab44b2.png",
        "width": 1959,
        "height": 803,
        "label": "IMAGE-009"
      },
      {
        "src": "/images/cases/history-20261030/asset_73224ae7eaf54473ab8f2744f3128968.png",
        "width": 1960,
        "height": 802,
        "label": "IMAGE-010"
      },
      {
        "src": "/images/cases/history-20261030/asset_781879952f0940ad9e896c54592ed612.png",
        "width": 1959,
        "height": 803,
        "label": "IMAGE-011"
      },
      {
        "src": "/images/cases/history-20261030/asset_e2436a3793f04c8ea83f548b261e3f5b.png",
        "width": 1961,
        "height": 802,
        "label": "IMAGE-012"
      },
      {
        "src": "/images/cases/history-20261030/asset_165523cd422d4b37ba1fe6d73ce0a960.png",
        "width": 1961,
        "height": 802,
        "label": "IMAGE-013"
      },
      {
        "src": "/images/cases/history-20261030/asset_b13b3580fbdb4fbb8cd586b0752c5a0c.png",
        "width": 1960,
        "height": 802,
        "label": "IMAGE-014"
      },
      {
        "src": "/images/cases/history-20261030/asset_08f3285da274465f83107e7894c0f6df.png",
        "width": 1448,
        "height": 1086,
        "label": "IMAGE-015"
      },
      {
        "src": "/images/cases/history-20261030/asset_826bfc9ab752412d86b537e4c73a2f3d.png",
        "width": 1448,
        "height": 1086,
        "label": "IMAGE-016"
      },
      {
        "src": "/images/cases/history-20261030/asset_dd7dc64e31af4a0cad5a46ea34ea14f0.png",
        "width": 1448,
        "height": 1086,
        "label": "IMAGE-017"
      },
      {
        "src": "/images/cases/history-20261030/asset_2d26096fab23449f9e7dc7b7158f4481.png",
        "width": 1448,
        "height": 1086,
        "label": "IMAGE-018"
      },
      {
        "src": "/images/cases/history-20261030/asset_4fa4d0cb36f54bcea06b9c54941902e2.png",
        "width": 1448,
        "height": 1086,
        "label": "IMAGE-019"
      },
      {
        "src": "/images/cases/history-20261030/asset_95651d0b3a474881b7d993aab7fc074d.png",
        "width": 1448,
        "height": 1086,
        "label": "IMAGE-020"
      },
      {
        "src": "/images/cases/history-20261030/asset_53592324df9442428217f27ee72fc9a2.png",
        "width": 1448,
        "height": 1086,
        "label": "IMAGE-021"
      }
    ],
    "taskId": "job_ef46a4463022450fa3af044da408e1df",
    "taskUrl": "https://huipin.haiyushuke.com/workspace/amazon-visual-suite?jobId=job_ef46a4463022450fa3af044da408e1df",
    "historyDate": "2026-10-30 09:58",
    "settings": "历史任务 · 亚马逊美国站 · 英语 · 快速版 · 21 项。输入栏名为白底商品图，实际上传的是包装实拍；预览保留真实输入，不另换白底图。",
    "note": "提取自 2026-10-30 09:58 的历史任务。2 张商品原图；已交付 21 / 21 张。原图、参考和结果均直接保留历史文件，未重新生成或二次修图。"
  },
  {
    "id": "color",
    "title": "商品换色",
    "product": "皮革手提包",
    "status": "completed",
    "benefit": "同一只包，棕色换成靛蓝色。只看配色的变化。",
    "inputs": [
      {
        "src": "/images/cases/platform-v3/01-bag-white.png",
        "label": "原图：白底棕色手提包"
      }
    ],
    "result": "/images/cases/platform-v3/color-result.png",
    "taskId": "job_5e02270cdeb84b65817dd43222e6d5be",
    "taskUrl": "https://huipin.haiyushuke.com/workspace/product-image/change-color?jobId=job_5e02270cdeb84b65817dd43222e6d5be",
    "settings": "天猫 / 淘宝 · 3:4 · 1 张。靛蓝色 #354D9B；仅修改包身、翻盖、提手和肩带的皮革颜色，保留原包轮廓、金色五金、锁扣、纹理、缝线、白底与光影。",
    "note": "网站真实输出 768 × 1024。用白底隔离背景干扰，更容易核对颜色、纹理和五金是否保留。"
  },
  {
    "id": "background",
    "title": "更换背景",
    "product": "奶油白耳机",
    "status": "completed",
    "benefit": "从杂乱办公桌，到干净的窗边商品场景。",
    "inputs": [
      {
        "src": "/images/cases/platform-v3/02-headphones-messy.png",
        "label": "原图：杂乱桌面随手拍"
      }
    ],
    "result": "/images/cases/platform-v3/background-result.png",
    "taskId": "job_eb9fe9bde269423ebb1a4b9952524331",
    "taskUrl": "https://huipin.haiyushuke.com/workspace/product-image/change-background?jobId=job_eb9fe9bde269423ebb1a4b9952524331",
    "settings": "天猫 / 淘宝 · 3:4 · 1 张。移除电脑、纸张、线材、纸盒与鼠标等桌面杂物，换为浅米石材桌、自然窗光、鼠尾草绿软椅与少量淡紫花朵；保留同一副耳机的结构、颜色和摆放角度。",
    "note": "网站真实输出 768 × 1024。右图没有再用内置生图加工，保留网站原始文件。"
  },
  {
    "id": "try-on",
    "title": "商品试穿",
    "product": "针织开衫",
    "status": "completed",
    "benefit": "平铺服装＋模特原图，查看衣服实际上身的变化。",
    "inputs": [
      {
        "src": "/images/cases/platform-v2/03-cardigan.png",
        "label": "商品原图：平铺开衫"
      },
      {
        "src": "/images/cases/platform-v2/04-model.png",
        "label": "参考原图：试穿前模特"
      }
    ],
    "result": "/images/cases/platform-v2/try-on-result.png",
    "taskId": "job_5dd20cac0d314981ae75657741870ec3",
    "taskUrl": "https://huipin.haiyushuke.com/workspace/product-image/try-on?jobId=job_5dd20cac0d314981ae75657741870ec3",
    "settings": "保留此前的真实试穿任务。开衫上传至商品图区，模特上传至参考图区；天猫 / 淘宝 · 3:4 · 1 张。要求保留人物身份、牛仔裤、白鞋和环境，并保留白色内搭。",
    "note": "这组输入已经符合功能对照思路，沿用网站真实输出 768 × 1024。实际结果景别变近，纽扣等服装细节仍需核对。"
  }
];
