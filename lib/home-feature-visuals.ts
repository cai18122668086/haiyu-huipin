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

export const HOME_FEATURE_VISUALS: Readonly<Record<string, HomeFeatureVisual>> = {
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
