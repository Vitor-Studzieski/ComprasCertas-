type JsonCredentialDescriptor = { id: string; type?: PublicKeyCredentialType; transports?: AuthenticatorTransport[] };

function decodeBase64Url(value: string): ArrayBuffer {
  const base64 = value.replace(/-/g, '+').replace(/_/g, '/');
  const binary = window.atob(base64.padEnd(Math.ceil(base64.length / 4) * 4, '='));
  const bytes = new Uint8Array(binary.length);
  for (let index = 0; index < binary.length; index += 1) bytes[index] = binary.charCodeAt(index);
  return bytes.buffer;
}

function encodeBase64Url(value: ArrayBuffer): string {
  const bytes = new Uint8Array(value);
  let binary = '';
  for (const byte of bytes) binary += String.fromCharCode(byte);
  return window.btoa(binary).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/g, '');
}

export async function createWebAuthnCredential(optionsJson: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!navigator.credentials?.create || !window.PublicKeyCredential) {
    throw new Error('Este navegador não oferece suporte a passkeys. Tente em um navegador compatível.');
  }

  const user = optionsJson.user as { id: string; [key: string]: unknown };
  const excludeCredentials = (optionsJson.excludeCredentials as JsonCredentialDescriptor[] | undefined)
    ?.map((item) => ({ ...item, id: decodeBase64Url(item.id) }));
  const publicKey = {
    ...optionsJson,
    challenge: decodeBase64Url(optionsJson.challenge as string),
    user: { ...user, id: decodeBase64Url(user.id) },
    excludeCredentials,
  } as unknown as PublicKeyCredentialCreationOptions;

  const credential = await navigator.credentials.create({ publicKey });
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('A criação da passkey foi cancelada.');
  }

  const response = credential.response as AuthenticatorAttestationResponse;
  return {
    id: credential.id,
    rawId: encodeBase64Url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: encodeBase64Url(response.clientDataJSON),
      attestationObject: encodeBase64Url(response.attestationObject),
      transports: response.getTransports?.() ?? [],
    },
  };
}

export async function getWebAuthnAssertion(optionsJson: Record<string, unknown>): Promise<Record<string, unknown>> {
  if (!navigator.credentials?.get || !window.PublicKeyCredential) {
    throw new Error('Este navegador não oferece suporte a passkeys.');
  }

  const allowCredentials = (optionsJson.allowCredentials as JsonCredentialDescriptor[] | undefined)
    ?.map((item) => ({ ...item, id: decodeBase64Url(item.id) }));
  const publicKey = {
    ...optionsJson,
    challenge: decodeBase64Url(optionsJson.challenge as string),
    allowCredentials,
  } as unknown as PublicKeyCredentialRequestOptions;

  const credential = await navigator.credentials.get({ publicKey });
  if (!(credential instanceof PublicKeyCredential)) {
    throw new Error('A verificação da passkey foi cancelada.');
  }

  const response = credential.response as AuthenticatorAssertionResponse;
  return {
    id: credential.id,
    rawId: encodeBase64Url(credential.rawId),
    type: credential.type,
    response: {
      clientDataJSON: encodeBase64Url(response.clientDataJSON),
      authenticatorData: encodeBase64Url(response.authenticatorData),
      signature: encodeBase64Url(response.signature),
      userHandle: response.userHandle ? encodeBase64Url(response.userHandle) : null,
    },
  };
}
