import { describe, expect, it } from "vitest";
import { forcaDaSenha, lerLinkDoEmail } from "./senha";

describe("link do e-mail", () => {
  it("le o token e o tipo do fragmento", () => {
    const token = "a".repeat(40);
    expect(lerLinkDoEmail(`#access_token=${token}&expires_in=3600&type=invite`)).toEqual({ token, tipo: "invite" });
    expect(lerLinkDoEmail(`#access_token=${token}&type=recovery`).tipo).toBe("recovery");
  });

  it("link com erro ou sem token vira link expirado", () => {
    expect(lerLinkDoEmail("#error=access_denied&error_description=Email+link+is+invalid+or+has+expired").token).toBeNull();
    expect(lerLinkDoEmail("").token).toBeNull();
    expect(lerLinkDoEmail("#access_token=curto").token).toBeNull();
  });
});

describe("forca da senha (mesma regra da API)", () => {
  it("exige 10+ caracteres, letra e numero", () => {
    expect(forcaDaSenha("curta1").ok).toBe(false);
    expect(forcaDaSenha("somenteletrasaqui").ok).toBe(false);
    expect(forcaDaSenha("1234567890").ok).toBe(false);
    expect(forcaDaSenha("aaaaaaaaa1").ok).toBe(false);
    expect(forcaDaSenha("Precatorio2026").ok).toBe(true);
  });

  it("explica o que falta", () => {
    expect(forcaDaSenha("abc1").dica).toBe("Faltam 6 caracteres.");
    expect(forcaDaSenha("abcdefghijk").dica).toBe("Inclua pelo menos um número.");
  });
});
