import type { ComponentPropsWithoutRef } from "react";

export const CUSTOMER_SERVICE_URL = "https://work.weixin.qq.com/kfid/kfc28d6af4de31987dc";

type CustomerServiceLinkProps = Pick<ComponentPropsWithoutRef<"a">,
  "aria-label" | "children" | "className" | "onClick"
>;

export function CustomerServiceLink({ children = "联系客服", ...props }: CustomerServiceLinkProps) {
  return (
    <a {...props} href={CUSTOMER_SERVICE_URL} target="_blank" rel="noopener noreferrer" title="联系海宇绘品客服">
      {children}
    </a>
  );
}
