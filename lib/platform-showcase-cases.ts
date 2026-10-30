export type ShowcaseCase = { id: string; inputs: { src: string; label: string }[]; additionalInputs?: { src: string; label: string }[]; results?: { src: string; label: string; width: number; height: number }[]; result?: string };

/** Approved public examples. Private task identifiers and history details stay local. */
export const PLATFORM_SHOWCASE_CASES: ShowcaseCase[] = [
  {
    "id": "replica",
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
    ]
  },
  {
    "id": "detail",
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
    ]
  },
  {
    "id": "domestic",
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
    ]
  },
  {
    "id": "amazon",
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
    ]
  },
  {
    "id": "color",
    "inputs": [
      {
        "src": "/images/cases/platform-v3/01-bag-white.png",
        "label": "原图：白底棕色手提包"
      }
    ],
    "result": "/images/cases/platform-v3/color-result.png"
  },
  {
    "id": "background",
    "inputs": [
      {
        "src": "/images/cases/platform-v3/02-headphones-messy.png",
        "label": "原图：杂乱桌面随手拍"
      }
    ],
    "result": "/images/cases/platform-v3/background-result.png"
  },
  {
    "id": "try-on",
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
    "result": "/images/cases/platform-v2/try-on-result.png"
  }
];
