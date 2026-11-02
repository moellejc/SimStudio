import { GetObjectCommand, S3Client } from "@aws-sdk/client-s3";

let client: S3Client | null = null;

export function s3() {
  if (client) return client;
  client = new S3Client({
    region: process.env.AWS_DEFAULT_REGION || "us-east-1",
    endpoint: process.env.S3_ENDPOINT_URL || undefined,
    forcePathStyle: true,
    credentials: {
      accessKeyId: process.env.AWS_ACCESS_KEY_ID || "test",
      secretAccessKey: process.env.AWS_SECRET_ACCESS_KEY || "test",
    },
  });
  return client;
}

export async function readObject(key: string) {
  const result = await s3().send(new GetObjectCommand({ Bucket: process.env.S3_BUCKET || "radlab-artifacts", Key: key }));
  const bytes = await result.Body?.transformToByteArray();
  if (!bytes) throw new Error("empty object");
  return { bytes, contentType: result.ContentType || "application/octet-stream" };
}
