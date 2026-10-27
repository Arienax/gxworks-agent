// Copyright 2026 Arienax
// Licensed under the Apache License, Version 2.0. See LICENSE for the license terms.

import type { Dispatch, SetStateAction } from "react";
export type Translate = (message: string) => string;
export type Setter<T> = Dispatch<SetStateAction<T>>;
export type Navigation = { setTab: Setter<string>; setPanel: Setter<string>; setModal: Setter<string> };
