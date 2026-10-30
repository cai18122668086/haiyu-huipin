export type HomeFeatureImage = {
  src: string;
  width: number;
  height: number;
};

export type HomeFeatureResponsiveImage = {
  desktop: HomeFeatureImage;
  tablet: HomeFeatureImage;
  mobile: HomeFeatureImage;
};

export type HomeFeatureVisual = {
  kind: "native" | "domestic";
  primary: HomeFeatureResponsiveImage;
  input: HomeFeatureResponsiveImage;
};

const image = (file: string, width: number, height: number): HomeFeatureImage => ({
  src: `/images/homepage-features/native-v2-webp/${file}`,
  width,
  height,
});

const responsive = (
  desktop: HomeFeatureImage,
  tablet: HomeFeatureImage,
  mobile: HomeFeatureImage,
): HomeFeatureResponsiveImage => ({ desktop, tablet, mobile });

const nativePrimary = (prefix: string) =>
  responsive(
    image(`${prefix}-primary-desktop.webp`, 1374, 1145),
    image(`${prefix}-primary-tablet.webp`, 1536, 1024),
    image(`${prefix}-primary-mobile.webp`, 1774, 887),
  );

const nativeInput = (prefix: string) =>
  responsive(
    image(`${prefix}-input-desktop.webp`, 1374, 1145),
    image(`${prefix}-input-tablet.webp`, 1536, 1024),
    image(`${prefix}-input-mobile.webp`, 1774, 887),
  );

const LEGACY_HOME_FEATURE_VISUALS: Readonly<Record<string, HomeFeatureVisual>> = {
  "local-edit": {
    kind: "native",
    primary: nativePrimary("01-masked"),
    input: nativeInput("01"),
  },
  "reference-remix": {
    kind: "native",
    primary: nativePrimary("03-remix"),
    input: nativeInput("03"),
  },
  "detail-pack": {
    kind: "native",
    primary: nativePrimary("04-detail"),
    input: nativeInput("04"),
  },
  "domestic-main-image": {
    kind: "domestic",
    primary: nativePrimary("05-domestic"),
    input: nativeInput("05"),
  },
  "amazon-visual-suite": {
    kind: "native",
    primary: nativePrimary("06-amazon"),
    input: nativeInput("06"),
  },
};

const CURATED_HOME_FEATURE_VISUALS: Readonly<Record<string, HomeFeatureVisual>> = {
  "one-click-replica": {
    "kind": "native",
    "primary": {
      "desktop": {
        "src": "/images/cases/olay-20261030/replica-01.png",
        "width": 1254,
        "height": 1254
      },
      "tablet": {
        "src": "/images/cases/olay-20261030/replica-01.png",
        "width": 1254,
        "height": 1254
      },
      "mobile": {
        "src": "/images/cases/olay-20261030/replica-01.png",
        "width": 1254,
        "height": 1254
      }
    },
    "input": {
      "desktop": {
        "src": "/images/cases/olay-20261030/product-01.webp",
        "width": 720,
        "height": 960
      },
      "tablet": {
        "src": "/images/cases/olay-20261030/product-01.webp",
        "width": 720,
        "height": 960
      },
      "mobile": {
        "src": "/images/cases/olay-20261030/product-01.webp",
        "width": 720,
        "height": 960
      }
    }
  },
  "detail-pack": {
    "kind": "native",
    "primary": {
      "desktop": {
        "src": "/images/cases/history-20261030/asset_84b0381b6cef439c8486c09ded379678.png",
        "width": 1086,
        "height": 1448
      },
      "tablet": {
        "src": "/images/cases/history-20261030/asset_84b0381b6cef439c8486c09ded379678.png",
        "width": 1086,
        "height": 1448
      },
      "mobile": {
        "src": "/images/cases/history-20261030/asset_84b0381b6cef439c8486c09ded379678.png",
        "width": 1086,
        "height": 1448
      }
    },
    "input": {
      "desktop": {
        "src": "/images/cases/history-20261030/asset_50341d648b31488eaa8f7696c34a3719.webp",
        "width": 720,
        "height": 960
      },
      "tablet": {
        "src": "/images/cases/history-20261030/asset_50341d648b31488eaa8f7696c34a3719.webp",
        "width": 720,
        "height": 960
      },
      "mobile": {
        "src": "/images/cases/history-20261030/asset_50341d648b31488eaa8f7696c34a3719.webp",
        "width": 720,
        "height": 960
      }
    }
  },
  "domestic-main-image": {
    "kind": "domestic",
    "primary": {
      "desktop": {
        "src": "/images/cases/history-20261030/asset_a47a47f308464d7ea2e928e8cc9f5c0c.png",
        "width": 1254,
        "height": 1254
      },
      "tablet": {
        "src": "/images/cases/history-20261030/asset_a47a47f308464d7ea2e928e8cc9f5c0c.png",
        "width": 1254,
        "height": 1254
      },
      "mobile": {
        "src": "/images/cases/history-20261030/asset_a47a47f308464d7ea2e928e8cc9f5c0c.png",
        "width": 1254,
        "height": 1254
      }
    },
    "input": {
      "desktop": {
        "src": "/images/cases/history-20261030/asset_a4f5437b884645d9bf65c88dccaebad8.webp",
        "width": 720,
        "height": 960
      },
      "tablet": {
        "src": "/images/cases/history-20261030/asset_a4f5437b884645d9bf65c88dccaebad8.webp",
        "width": 720,
        "height": 960
      },
      "mobile": {
        "src": "/images/cases/history-20261030/asset_a4f5437b884645d9bf65c88dccaebad8.webp",
        "width": 720,
        "height": 960
      }
    }
  },
  "amazon-visual-suite": {
    "kind": "native",
    "primary": {
      "desktop": {
        "src": "/images/cases/history-20261030/asset_20b9cb76cf2e4be2b84475b3ce21cd71.png",
        "width": 1254,
        "height": 1254
      },
      "tablet": {
        "src": "/images/cases/history-20261030/asset_20b9cb76cf2e4be2b84475b3ce21cd71.png",
        "width": 1254,
        "height": 1254
      },
      "mobile": {
        "src": "/images/cases/history-20261030/asset_20b9cb76cf2e4be2b84475b3ce21cd71.png",
        "width": 1254,
        "height": 1254
      }
    },
    "input": {
      "desktop": {
        "src": "/images/cases/history-20261030/asset_18546d61a37b4ea8a6b318a854d81daa.webp",
        "width": 720,
        "height": 960
      },
      "tablet": {
        "src": "/images/cases/history-20261030/asset_18546d61a37b4ea8a6b318a854d81daa.webp",
        "width": 720,
        "height": 960
      },
      "mobile": {
        "src": "/images/cases/history-20261030/asset_18546d61a37b4ea8a6b318a854d81daa.webp",
        "width": 720,
        "height": 960
      }
    }
  },
  "change-background": {
    "kind": "native",
    "primary": {
      "desktop": {
        "src": "/images/cases/platform-v3/background-result.png",
        "width": 768,
        "height": 1024
      },
      "tablet": {
        "src": "/images/cases/platform-v3/background-result.png",
        "width": 768,
        "height": 1024
      },
      "mobile": {
        "src": "/images/cases/platform-v3/background-result.png",
        "width": 768,
        "height": 1024
      }
    },
    "input": {
      "desktop": {
        "src": "/images/cases/platform-v3/02-headphones-messy.png",
        "width": 1086,
        "height": 1448
      },
      "tablet": {
        "src": "/images/cases/platform-v3/02-headphones-messy.png",
        "width": 1086,
        "height": 1448
      },
      "mobile": {
        "src": "/images/cases/platform-v3/02-headphones-messy.png",
        "width": 1086,
        "height": 1448
      }
    }
  },
  "change-color": {
    "kind": "native",
    "primary": {
      "desktop": {
        "src": "/images/cases/platform-v3/color-result.png",
        "width": 768,
        "height": 1024
      },
      "tablet": {
        "src": "/images/cases/platform-v3/color-result.png",
        "width": 768,
        "height": 1024
      },
      "mobile": {
        "src": "/images/cases/platform-v3/color-result.png",
        "width": 768,
        "height": 1024
      }
    },
    "input": {
      "desktop": {
        "src": "/images/cases/platform-v3/01-bag-white.png",
        "width": 1086,
        "height": 1448
      },
      "tablet": {
        "src": "/images/cases/platform-v3/01-bag-white.png",
        "width": 1086,
        "height": 1448
      },
      "mobile": {
        "src": "/images/cases/platform-v3/01-bag-white.png",
        "width": 1086,
        "height": 1448
      }
    }
  },
  "try-on": {
    "kind": "native",
    "primary": {
      "desktop": {
        "src": "/images/cases/platform-v2/try-on-result.png",
        "width": 768,
        "height": 1024
      },
      "tablet": {
        "src": "/images/cases/platform-v2/try-on-result.png",
        "width": 768,
        "height": 1024
      },
      "mobile": {
        "src": "/images/cases/platform-v2/try-on-result.png",
        "width": 768,
        "height": 1024
      }
    },
    "input": {
      "desktop": {
        "src": "/images/cases/platform-v2/03-cardigan.png",
        "width": 1086,
        "height": 1448
      },
      "tablet": {
        "src": "/images/cases/platform-v2/03-cardigan.png",
        "width": 1086,
        "height": 1448
      },
      "mobile": {
        "src": "/images/cases/platform-v2/03-cardigan.png",
        "width": 1086,
        "height": 1448
      }
    }
  },
  "reference-remix": {
    "kind": "native",
    "primary": {
      "desktop": {
        "src": "/images/homepage-features/card-03-remix-result-serum-v2.png",
        "width": 1122,
        "height": 1402
      },
      "tablet": {
        "src": "/images/homepage-features/card-03-remix-result-serum-v2.png",
        "width": 1122,
        "height": 1402
      },
      "mobile": {
        "src": "/images/homepage-features/card-03-remix-result-serum-v2.png",
        "width": 1122,
        "height": 1402
      }
    },
    "input": {
      "desktop": {
        "src": "/images/homepage-features/native-v2-webp/03-input-desktop.webp",
        "width": 1374,
        "height": 1145
      },
      "tablet": {
        "src": "/images/homepage-features/native-v2-webp/03-input-desktop.webp",
        "width": 1374,
        "height": 1145
      },
      "mobile": {
        "src": "/images/homepage-features/native-v2-webp/03-input-desktop.webp",
        "width": 1374,
        "height": 1145
      }
    }
  }
};

export const HOME_FEATURE_VISUALS: Readonly<Record<string, HomeFeatureVisual>> = { ...LEGACY_HOME_FEATURE_VISUALS, ...CURATED_HOME_FEATURE_VISUALS };
