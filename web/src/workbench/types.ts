import type { Dispatch, SetStateAction } from "react";
export type Translate = (message: string) => string;
export type Setter<T> = Dispatch<SetStateAction<T>>;
export type Navigation = { setTab: Setter<string>; setPanel: Setter<string>; setModal: Setter<string> };
